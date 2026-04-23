
# --- MULTI-SOURCE CHATBOT TASKS ---
import logging
from tasks import huey, update_task_progress
from utils.multi_source_tasks import (
    process_whatsapp_source as _process_whatsapp,
    process_website_source as _process_website,
    process_pdf_source as _process_pdf,
    update_chatbot_readiness
)

logger = logging.getLogger(__name__)


@huey.task(context=True)
def process_whatsapp_source_task(source_id: int, file_path: str, preferred_agent: str = None, task=None):
    """
    Huey task wrapper for processing WhatsApp chat exports.
    
    Args:
        source_id: ID of the data source record
        file_path: Path to the WhatsApp chat export file
        preferred_agent: Optional name of the support agent/receptionist to learn from
        task: Huey task context
    """
    task_id = task.id if task else None
    
    try:
        print(f"\n{'═'*60}")
        print(f"📱 WHATSAPP SOURCE {source_id} — Processing started...")
        print(f"   Agent: {preferred_agent or 'auto-detect'}")
        print(f"{'═'*60}")
        logger.info(f"[WHATSAPP TASK] Starting processing for source {source_id}, preferred_agent={preferred_agent}")
        update_task_progress(task_id, 'processing', 5, 'Processing WhatsApp chat...')
        
        result = _process_whatsapp(source_id, file_path, task_id, preferred_agent)
        
        update_task_progress(task_id, 'complete', 100, 'WhatsApp chat processed successfully!')
        print(f"\n{'═'*60}")
        print(f"✅ WHATSAPP SOURCE {source_id} — READY!")
        print(f"   {result}")
        print(f"{'═'*60}\n")
        logger.info(f"[WHATSAPP TASK] Completed: {result}")
        return result
        
    except Exception as e:
        print(f"\n{'═'*60}")
        print(f"❌ WHATSAPP SOURCE {source_id} — FAILED!")
        print(f"   Error: {e}")
        print(f"{'═'*60}\n")
        logger.error(f"[WHATSAPP TASK] Failed for source {source_id}: {e}", exc_info=True)
        update_task_progress(task_id, 'failed', 0, str(e))
        raise


@huey.task(context=True)
def process_website_source_task(source_id: int, task=None):
    """
    Huey task wrapper for processing website sources.
    
    Args:
        source_id: ID of the data source record
        task: Huey task context
    """
    task_id = task.id if task else None
    
    try:
        print(f"\n{'═'*60}")
        print(f"🌐 WEBSITE SOURCE {source_id} — Processing started...")
        print(f"{'═'*60}")
        logger.info(f"[WEBSITE TASK] Starting processing for source {source_id}")
        update_task_progress(task_id, 'processing', 5, 'Crawling website...')
        
        result = _process_website(source_id, task_id)
        
        update_task_progress(task_id, 'complete', 100, 'Website scraped successfully!')
        print(f"\n{'═'*60}")
        print(f"✅ WEBSITE SOURCE {source_id} — READY!")
        print(f"   {result}")
        print(f"{'═'*60}\n")
        logger.info(f"[WEBSITE TASK] Completed: {result}")
        return result
        
    except Exception as e:
        print(f"\n{'═'*60}")
        print(f"❌ WEBSITE SOURCE {source_id} — FAILED!")
        print(f"   Error: {e}")
        print(f"{'═'*60}\n")
        logger.error(f"[WEBSITE TASK] Failed for source {source_id}: {e}", exc_info=True)
        update_task_progress(task_id, 'failed', 0, str(e))
        raise


@huey.task(context=True)
def process_pdf_source_task(source_id: int, file_path: str, task=None):
    """
    Huey task wrapper for processing PDF file uploads.

    Args:
        source_id: ID of the data source record
        file_path: Path to the uploaded PDF file
        task: Huey task context
    """
    task_id = task.id if task else None

    try:
        print(f"\n{'═'*60}")
        print(f"📄 PDF SOURCE {source_id} — Processing started...")
        print(f"   File: {file_path}")
        print(f"{'═'*60}")
        logger.info(f"[PDF TASK] Starting processing for source {source_id}")
        update_task_progress(task_id, 'processing', 5, 'Reading PDF...')

        result = _process_pdf(source_id, file_path, task_id)

        update_task_progress(task_id, 'complete', 100, 'PDF processed successfully!')
        print(f"\n{'═'*60}")
        print(f"✅ PDF SOURCE {source_id} — READY!")
        print(f"   {result}")
        print(f"{'═'*60}\n")
        logger.info(f"[PDF TASK] Completed: {result}")
        return result

    except Exception as e:
        print(f"\n{'═'*60}")
        print(f"❌ PDF SOURCE {source_id} — FAILED!")
        print(f"   Error: {e}")
        print(f"{'═'*60}\n")
        logger.error(f"[PDF TASK] Failed for source {source_id}: {e}", exc_info=True)
        update_task_progress(task_id, 'failed', 0, str(e))
        raise
