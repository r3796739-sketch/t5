import asyncio
import os
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
from typing import Dict, List

# --- Local Utils ---
from utils.supabase_client import get_supabase_admin_client
from utils.discord_utils import YoppyBot # This is the class for Branded Bots
from discord_service_shared_bot import SharedYoppyBot # Import the Shared Bot class

# --- Setup ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

# --- Configuration ---
SHARED_BOT_TOKEN = os.environ.get("DISCORD_SHARED_BOT_TOKEN")
SYNC_INTERVAL_SECONDS = 20 # How often to check the database for changes

class BotManager:
    """
    Manages the lifecycle of all Discord bots (shared and branded)
    as a single, standalone, asynchronous service.
    """
    def __init__(self):
        self.supabase = get_supabase_admin_client()
        # A dictionary to keep track of running branded bots: {bot_id: asyncio.Task}
        self.running_branded_bots: Dict[int, asyncio.Task] = {}
        self.shared_bot_task: asyncio.Task = None

    async def start_branded_bot(self, bot_id: int, token: str):
        """Creates, runs, and stores a task for a single branded bot."""
        if bot_id in self.running_branded_bots:
            log.warning(f"Bot {bot_id} is already running. Skipping start.")
            return

        log.info(f"Starting branded bot with ID: {bot_id}")
        try:
            # Create a task that runs the bot.
            # The YoppyBot class from discord_utils is used here.
            task = asyncio.create_task(self._run_bot_instance(YoppyBot, token, bot_id))
            self.running_branded_bots[bot_id] = task
        except Exception as e:
            log.error(f"Failed to start branded bot {bot_id}: {e}", exc_info=True)

    async def stop_branded_bot(self, bot_id: int):
        """Stops a running branded bot task and removes it from tracking."""
        if bot_id not in self.running_branded_bots:
            log.warning(f"Bot {bot_id} is not running. Skipping stop.")
            return

        log.info(f"Stopping branded bot with ID: {bot_id}")
        task = self.running_branded_bots.pop(bot_id)
        if task and not task.done():
            task.cancel()
        log.info(f"Bot {bot_id} has been stopped.")

    async def _run_bot_instance(self, bot_class, token: str, db_id: int = None):
        """Internal helper to run a bot instance and handle cleanup."""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        
        bot_instance = bot_class(
            command_prefix="!unused", 
            intents=intents, 
            bot_token=token, 
            bot_db_id=db_id
        )
        try:
            await bot_instance.start(token)
        except discord.LoginFailure:
            log.error(f"Login failed for bot ID {db_id}. The token is likely invalid.")
            # Set the bot's status to error in the DB so it's not picked up again
            if db_id:
                self.supabase.table('discord_bots').update({'status': 'error'}).eq('id', db_id).execute()
        except asyncio.CancelledError:
            log.info(f"Bot ID {db_id or 'Shared Bot'} task was cancelled. Closing connection.")
        except Exception as e:
            log.error(f"Bot ID {db_id or 'Shared Bot'} crashed with an unexpected error: {e}", exc_info=True)
        finally:
            if not bot_instance.is_closed():
                await bot_instance.close()
            log.info(f"Bot ID {db_id or 'Shared Bot'} has been shut down.")

    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def sync_bots_with_db(self):
        """The main sync loop to keep running bots in sync with the database."""
        log.info("--- Syncing bots with database... ---")
        try:
            response = self.supabase.table('discord_bots').select('id, bot_token, status').execute()
            if not response.data:
                db_bots = []
            else:
                db_bots = response.data

            # Create a set of bot IDs that should be online according to the database
            db_online_bots = {bot['id']: bot['bot_token'] for bot in db_bots if bot['status'] == 'online'}
            
            # Create a set of bot IDs that are currently running in our manager
            running_bot_ids = set(self.running_branded_bots.keys())

            # --- Start new bots ---
            # Find bots that are in the DB as 'online' but not in our running set
            bots_to_start = db_online_bots.keys() - running_bot_ids
            for bot_id in bots_to_start:
                token = db_online_bots[bot_id]
                await self.start_branded_bot(bot_id, token)

            # --- Stop old bots ---
            # Find bots that are running but are no longer 'online' in the DB
            bots_to_stop = running_bot_ids - db_online_bots.keys()
            for bot_id in bots_to_stop:
                await self.stop_branded_bot(bot_id)

            log.info(f"Sync complete. Running branded bots: {len(self.running_branded_bots)}")

        except Exception as e:
            log.error(f"CRITICAL ERROR during bot sync loop: {e}", exc_info=True)

    @sync_bots_with_db.before_loop
    async def before_sync_loop(self):
        log.info("Waiting for shared bot to be ready before starting sync loop...")
        if self.shared_bot_task:
            # This assumes the shared bot instance is accessible and has a 'wait_until_ready'
            # In a real scenario, you'd need a more robust way to check readiness.
            # For now, a simple sleep is sufficient to allow login.
            await asyncio.sleep(5)
        log.info("Sync loop is starting.")

    async def start_shared_bot(self):
        """Starts the main shared YoppyChat bot."""
        if not SHARED_BOT_TOKEN:
            log.warning("SHARED_BOT_TOKEN not set. Cannot start the shared bot.")
            return

        log.info("Starting the SHARED YoppyChat bot...")
        try:
            # We use the same helper to run the instance of the Shared Bot
            self.shared_bot_task = asyncio.create_task(
                self._run_bot_instance(SharedYoppyBot, SHARED_BOT_TOKEN)
            )
        except Exception as e:
            log.error(f"Failed to start the shared bot: {e}", exc_info=True)

    async def run(self):
        """The main entry point to start the manager and all bot services."""
        await self.start_shared_bot()
        self.sync_bots_with_db.start()
        
        # Keep the main manager task alive
        await asyncio.gather(
            self.shared_bot_task,
            *self.running_branded_bots.values(),
            return_exceptions=True
        )

if __name__ == "__main__":
    manager = BotManager()
    try:
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        log.info("Manager service shutting down by request.")