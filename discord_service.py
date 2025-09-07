import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
from typing import List
import json
from utils.qa_utils import answer_question_stream
# utils imports
from utils import db_utils
from utils.history_utils import get_chat_history_for_service
# --- Setup ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# --- Configuration ---
SHARED_BOT_TOKEN = os.environ.get("DISCORD_SHARED_BOT_TOKEN")
if not SHARED_BOT_TOKEN:
    log.error("DISCORD_SHARED_BOT_TOKEN not found. Bot cannot start.")
    exit()

intents = discord.Intents.default()
intents.message_content = True

# =============================================================================
# === UI View for the 'Sources' Button ===
# =============================================================================
class SourcesView(discord.ui.View):
    def __init__(self, *, sources: List[dict]):
        super().__init__(timeout=300)  # Button will be active for 5 minutes
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

# =============================================================================
# === Main Bot Class ===
# =============================================================================
class SharedYoppyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(command_prefix="!unused", intents=intents)
        self.history_cache = {}

    async def setup_hook(self):
        self.tree.add_command(link_channel_command)
        await self.tree.sync()
        log.info("Slash commands registered and synced in setup_hook.")

    async def on_ready(self):
        log.info(f'Shared Bot Logged in as {self.user} (ID: {self.user.id})')

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        if self.user.mentioned_in(message):
            await message.channel.typing()
            server_id = message.guild.id
            log.info(f"Bot mentioned in server {server_id}")

            server_link = db_utils.get_discord_server_link(server_id)
            if not server_link:
                await message.reply("This server isn't linked to a YouTube channel yet. An admin can link one using the `/link_channel` command.")
                return

            channel_id = server_link.get('linked_channel_id')
            channel_data = db_utils.get_channel_by_id(channel_id)
            if not channel_data:
                await message.reply("I can't seem to find the data for the linked YouTube channel. Please try linking it again.")
                return

            question = message.content.replace(f'<@{self.user.id}>', '').strip()
            owner_user_id = server_link.get('owner_user_id')

            # --- UPGRADED MEMORY LOGIC WITH CACHE ---
            conversation_id = f"discord_{message.channel.id}"
            
            db_history = get_chat_history_for_service(
                user_id=owner_user_id, 
                channel_name=conversation_id, 
                limit=20
            )
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
            
            full_answer = ""
            sources = []
            
            try:
                stream = answer_question_stream(
                    question_for_prompt=final_question_with_history,
                    question_for_search=question,
                    channel_data=channel_data,
                    user_id=owner_user_id,
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

                # --- NEW BUTTON LOGIC ---
                view = SourcesView(sources=sources) if sources else None

                if len(full_answer) > 2000:
                    await message.reply(full_answer[:1990] + "...", view=view)
                else:
                    await message.reply(full_answer, view=view)

            except Exception as e:
                log.error(f"Error getting AI answer for server {server_id}: {e}", exc_info=True)
                await message.reply("Sorry, an error occurred while trying to find an answer.")

# =============================================================================
# === Slash Command Definitions ===
# =============================================================================
async def channel_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Provides autocomplete suggestions for the 'channel' option in the /link_channel command.
    """
    user_channels = db_utils.get_user_channels_by_discord_id(interaction.user.id)
    if not user_channels:
        return []
    choices = [
        app_commands.Choice(name=ch['channel_name'], value=str(ch['id']))
        for ch in user_channels 
        if current.lower() in ch['channel_name'].lower() and ch.get('channel_name')
    ]
    return choices[:25]

@app_commands.command(name="link_channel", description="Link this server to one of your YoppyChat channels.")
@app_commands.autocomplete(channel=channel_autocomplete)
@app_commands.describe(channel="The name of the channel you want to link from your account.")
@app_commands.checks.has_permissions(administrator=True)
async def link_channel_command(interaction: discord.Interaction, channel: str):
    """
    The command that an admin runs to link a Discord server to a YouTube channel.
    """
    await interaction.response.defer(ephemeral=True)
    try:
        channel_id = int(channel)
        app_user = db_utils.find_app_user_by_discord_id(interaction.user.id)
        if not app_user:
            await interaction.followup.send("Your Discord account isn't linked to a YoppyChat account. Please link it on the website first.")
            return

        db_utils.link_discord_server_to_channel(interaction.guild.id, channel_id, app_user['id'])
        
        channel_details = db_utils.get_channel_by_id(channel_id)
        channel_name = channel_details.get('channel_name', 'your selected channel') if channel_details else 'your selected channel'

        await interaction.followup.send(f"✅ Success! This server is now linked to **{channel_name}**.")

    except ValueError:
        await interaction.followup.send("❌ Invalid channel selected. Please choose one from the list.")
    except Exception as e:
        log.error(f"Error in /link_channel command: {e}", exc_info=True)
        await interaction.followup.send("❌ An unexpected error occurred. Please try again.")
# --- END: SLASH COMMAND DEFINITION ---


import asyncio

if __name__ == "__main__":
    bot = SharedYoppyBot(intents=intents)
    try:
        bot.run(SHARED_BOT_TOKEN)
    finally:
        # Workaround for 'Event loop is closed' RuntimeError on Windows
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass