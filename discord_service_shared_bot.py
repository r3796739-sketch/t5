import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import List
import json

# --- Local Utils ---
from utils import db_utils
from utils.qa_utils import answer_question_stream
from utils.history_utils import get_chat_history_for_service

# --- Setup ---
log = logging.getLogger(__name__)

# =============================================================================
# === UI View for the 'Sources' Button ===
# =============================================================================
class SourcesView(discord.ui.View):
    def __init__(self, *, sources: List[dict]):
        super().__init__(timeout=300)
        self.sources = sources

    @discord.ui.button(label="Sources", style=discord.ButtonStyle.secondary, emoji="📚")
    async def show_sources_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.sources:
            await interaction.response.send_message("No sources were found for this answer.", ephemeral=True)
            return
        source_links = "\n".join([f"- [{s['title']}]({s['url']})" for s in self.sources])
        embed = discord.Embed(title="Sources", description=source_links, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        button.disabled = True
        await interaction.message.edit(view=self)

# =============================================================================
# === Autocomplete Function ===
# =============================================================================
async def channel_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    user_channels = db_utils.get_user_channels_by_discord_id(interaction.user.id)
    if not user_channels:
        return []
    choices = [
        app_commands.Choice(name=ch['channel_name'], value=str(ch['id']))
        for ch in user_channels 
        if current.lower() in ch['channel_name'].lower() and ch.get('channel_name')
    ]
    return choices[:25]

# =============================================================================
# === Main Shared Bot Class ===
# =============================================================================
class SharedYoppyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history_cache = {}

    async def setup_hook(self):
        # Manually create and add the command to the tree
        link_channel_cmd = app_commands.Command(
            name="link_channel",
            description="Link this server to one of your YoppyChat channels.",
            callback=self.link_channel_command_callback
        )
        link_channel_cmd.guild_only = True
        link_channel_cmd.default_permissions = discord.Permissions(administrator=True)
        self.tree.add_command(link_channel_cmd)
        await self.tree.sync()

    async def on_ready(self):
        log.info(f'SHARED BOT Logged in as {self.user} (ID: {self.user.id})')

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        if self.user.mentioned_in(message):
            await message.channel.typing()
            server_id = message.guild.id
            log.info(f"SHARED BOT mentioned in server {server_id}")

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
            conversation_id = f"discord_{message.channel.id}"
            
            db_history = get_chat_history_for_service(user_id=owner_user_id, channel_name=conversation_id, limit=20)
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

                view = SourcesView(sources=sources) if sources else None

                if len(full_answer) > 2000:
                    await message.reply(full_answer[:1990] + "...", view=view)
                else:
                    await message.reply(full_answer, view=view)

            except Exception as e:
                log.error(f"Error getting AI answer for server {server_id}: {e}", exc_info=True)
                await message.reply("Sorry, an error occurred while trying to find an answer.")

    @app_commands.autocomplete(channel=channel_autocomplete)
    @app_commands.describe(channel="The name of the channel you want to link from your account.")
    async def link_channel_command_callback(self, interaction: discord.Interaction, channel: str):
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