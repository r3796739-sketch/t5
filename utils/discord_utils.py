import discord
from discord.ext import commands, tasks
import aiohttp
from utils.youtube_utils import get_channel_details_by_url
import logging
from utils import db_utils
import json
import base64
from .supabase_client import get_supabase_admin_client
from utils.history_utils import get_chat_history_for_service
import os
from supabase import create_client, Client
import asyncio

log = logging.getLogger(__name__)

class YoppyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot_token = kwargs.get('bot_token')
        self.bot_db_id = kwargs.get('bot_db_id')
        self.history_cache = {}

    async def setup_hook(self):
        """This is called once the bot is ready and correctly starts the background task."""
        self.check_status_periodically.start()

    @tasks.loop(seconds=2)
    async def check_status_periodically(self):
        """Periodically checks the database for a shutdown signal."""
        try:
            url: str = os.environ.get("SUPABASE_URL")
            key: str = os.environ.get("SUPABASE_SERVICE_KEY")
            if not url or not key:
                log.error("Supabase credentials not found in status check loop.")
                return
            
            supabase_admin: Client = create_client(url, key)
            
            # --- START: THE FIX ---
            # Use maybe_single() to gracefully handle cases where the bot has been deleted.
            response = supabase_admin.table('discord_bots').select('status').eq('id', self.bot_db_id).maybe_single().execute()

            # If response.data is None, it means the bot was deleted from the DB.
            if not response.data:
                log.warning(f"Bot ID {self.bot_db_id} not found in DB (likely deleted). Shutting down task.")
                await self.close()
                self.check_status_periodically.stop()
                return # Exit the function to prevent further processing
            # --- END: THE FIX ---

            db_status = response.data.get('status')
            log.info(f"Bot ID {self.bot_db_id} periodic status check. Status in DB: '{db_status}'")

            if db_status == 'offline':
                log.info(f"Bot ID {self.bot_db_id} received 'offline' signal from DB. Shutting down...")
                await self.close()
                self.check_status_periodically.stop()

        except Exception as e:
            log.error(f"CRITICAL Error in status check loop for bot {self.bot_db_id}: {e}", exc_info=True)

    @check_status_periodically.before_loop
    async def before_status_check(self):
        """Ensures the bot is fully connected before the loop starts."""
        await self.wait_until_ready()

    async def on_ready(self):
        log.info(f'Logged in as {self.user} (ID: {self.user.id})')
        await self.tree.sync()
        
        if self.bot_db_id:
            try:
                supabase_admin = get_supabase_admin_client()
                response = supabase_admin.table('discord_bots').update({'status': 'online'}).eq('id', self.bot_db_id).execute()
                
                if response.data:
                    log.info(f"Successfully updated status to ONLINE for bot ID {self.bot_db_id}")
                else:
                    log.error(f"Failed to update status for bot ID {self.bot_db_id}.")
            except Exception as e:
                log.error(f"DATABASE ERROR in on_ready for bot ID {self.bot_db_id}: {e}", exc_info=True)
        else:
            log.warning("Bot is running without a database ID (bot_db_id), so it cannot update its status.")

    async def on_message(self, message):
        if message.author == self.user:
            return

        if self.user.mentioned_in(message):
            await message.channel.typing()
            
            log.info(f"Bot was mentioned in a message: {message.content}")

            bot_data = await asyncio.to_thread(db_utils.get_discord_bot, self.bot_token)
            
            if not bot_data or bot_data.get('status') != 'online':
                # Silently ignore mentions if the bot is not active and online
                return

            channel_id = bot_data.get('youtube_channel_id')
            channel_data = db_utils.get_channel_by_id(channel_id)
            if not channel_data:
                await message.reply("The linked YouTube channel could not be found.")
                return

            question = message.content.replace(f'<@{self.user.id}>', '').replace(f'<@!{self.user.id}>', '').strip()
            user_id = bot_data.get('user_id')
            conversation_id = f"discord_{message.channel.id}"
            
            db_history = get_chat_history_for_service(user_id=user_id, channel_name=conversation_id, limit=20)
            cache_history = self.history_cache.get(conversation_id, [])
            
            combined_history = {f"{qa['question']}_{qa['answer']}": qa for qa in db_history}
            combined_history.update({f"{qa['question']}_{qa['answer']}": qa for qa in cache_history})
            
            history = list(combined_history.values())
            history = sorted(history, key=lambda qa: qa.get('created_at', ''))[-20:]
            
            chat_history_for_prompt = ""
            if history:
                log.info(f"Found {len(history)} combined messages for conversation {conversation_id}.")
                for qa in history:
                    chat_history_for_prompt += f"Human: {qa['question']}\nAI: {qa['answer']}\n\n"

            final_question_with_history = question
            if chat_history_for_prompt:
                final_question_with_history = (
                    f"Given the following conversation history:\n{chat_history_for_prompt}"
                    f"--- End History ---\n\n"
                    f"Now, answer this new question, considering the history as context:\n{question}"
                )

            from utils.qa_utils import answer_question_stream

            full_answer = ""
            sources = []
            
            try:
                stream = answer_question_stream(
                    question_for_prompt=final_question_with_history,
                    question_for_search=question,
                    channel_data=channel_data,
                    user_id=user_id,
                    access_token=None,
                    conversation_id=conversation_id
                )

                for chunk in stream:
                    if chunk.startswith('data: '):
                        data_str = chunk.replace('data: ', '').strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            if data.get('answer'):
                                full_answer += data['answer']
                            if data.get('sources'):
                                sources = data['sources']
                        except json.JSONDecodeError:
                            continue
                
                if full_answer:
                    if conversation_id not in self.history_cache:
                        self.history_cache[conversation_id] = []
                    
                    self.history_cache[conversation_id].append({'question': question, 'answer': full_answer})
                    self.history_cache[conversation_id] = self.history_cache[conversation_id][-20:]
                    log.info(f"Updated in-memory cache for {conversation_id}. Cache size: {len(self.history_cache[conversation_id])}")

                if not full_answer:
                    full_answer = "I couldn't find an answer to that in the channel's videos."

                view = SourcesView(sources=sources) if sources else None

                if len(full_answer) > 2000:
                    await message.reply(full_answer[:1990] + "...", view=view)
                else:
                    await message.reply(full_answer, view=view)

            except Exception as e:
                log.error(f"Error getting AI answer for bot {self.bot_db_id}: {e}", exc_info=True)
                await message.reply("Sorry, an error occurred while trying to find an answer.")

class SourcesView(discord.ui.View):
    def __init__(self, *, sources: list):
        super().__init__(timeout=300)
        self.sources = sources

    @discord.ui.button(label="Sources", style=discord.ButtonStyle.secondary, emoji="📚")
    async def show_sources_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.sources:
            await interaction.response.send_message("No sources were found for this answer.", ephemeral=True)
            return

        source_links = "\n".join([f"- [{s['title']}]({s['url']})" for s in self.sources])
        embed = discord.Embed(
            title="Sources",
            description=source_links,
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        button.disabled = True
        await interaction.message.edit(view=self)

async def update_bot_profile(bot_token: str, channel_url: str):
    try:
        log.info(f"Fetching YouTube channel details for URL: {channel_url}")
        channel_details = get_channel_details_by_url(channel_url)
        if not channel_details:
            return False, "Could not fetch YouTube channel details."

        new_name = channel_details['snippet']['title']
        thumbnail_url = channel_details['snippet']['thumbnails']['high']['url']
        log.info(f"Found channel: {new_name}, Thumbnail: {thumbnail_url}")

        payload = {'username': new_name}
        headers = {'Authorization': f'Bot {bot_token}', 'Content-Type': 'application/json'}

        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail_url) as resp:
                if resp.status == 200:
                    avatar_bytes = await resp.read()
                    b64_avatar = base64.b64encode(avatar_bytes).decode('utf-8')
                    payload['avatar'] = f'data:image/jpeg;base64,{b64_avatar}'
                else:
                    log.warning(f"Could not download avatar. Status: {resp.status}")

            api_url = 'https://discord.com/api/v10/users/@me'
            async with session.patch(api_url, headers=headers, json=payload) as patch_resp:
                if patch_resp.status == 200:
                    log.info("Successfully updated Discord bot profile via REST API.")
                    return True, f"Successfully updated bot profile to match '{new_name}'."
                else:
                    error_text = await patch_resp.text()
                    log.error(f"Discord API error during profile update: {patch_resp.status} - {error_text}")
                    return False, f"Discord API Error: {error_text}"

    except Exception as e:
        log.error(f"An unexpected error occurred in update_bot_profile: {e}", exc_info=True)
        return False, str(e)

def run_bot(bot_token: str, bot_db_id: int):
    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True

    bot = YoppyBot(command_prefix="!", intents=intents, bot_token=bot_token, bot_db_id=bot_db_id)

    @bot.tree.command(name="activate_qa", description="Activates the Q&A functionality for this server.")
    async def activate_qa(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return

        server_id = interaction.guild.id
        bot_data = db_utils.activate_discord_bot(bot.bot_token, server_id)

        if bot_data:
            await interaction.response.send_message("Q&A functionality has been activated for this server.", ephemeral=True)
        else:
            await interaction.response.send_message("There was an error activating the bot. Please make sure the bot token is correct.", ephemeral=True)
    
    bot.run(bot_token)