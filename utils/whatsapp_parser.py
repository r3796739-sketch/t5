"""
WhatsApp Chat Parser Utility
Parses WhatsApp chat export files and extracts structured message data
for creating chatbot training data.
"""

import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from collections import Counter
import logging

logger = logging.getLogger(__name__)


class WhatsAppParser:
    """
    Parser for WhatsApp chat export files.
    Supports multiple date/time formats used by WhatsApp exports.
    """
    
    # Common WhatsApp export patterns
    PATTERNS = [
        # 25/12/2025, 12:50 pm - nikhilrathore127: Message (Indian format with lowercase am/pm)
        r'(\d{1,2}/\d{1,2}/\d{2,4},\s\d{1,2}:\d{2}\s(?:am|pm|AM|PM))\s-\s([^:]+):\s(.+)',
        # [12/31/23, 10:30:45 PM] John: Hello (with brackets)
        r'\[(\d{1,2}/\d{1,2}/\d{2,4},\s\d{1,2}:\d{2}:\d{2}\s(?:am|pm|AM|PM))\]\s([^:]+):\s(.+)',
        # 12/31/23, 22:30 - John: Hello (24-hour format)
        r'(\d{1,2}/\d{1,2}/\d{2,4},\s\d{1,2}:\d{2})\s-\s([^:]+):\s(.+)',
        # 2023-12-31, 22:30 - John: Hello (ISO format)
        r'(\d{4}-\d{2}-\d{2},\s\d{1,2}:\d{2})\s-\s([^:]+):\s(.+)',
    ]
    
    def __init__(self):
        # Compile patterns with IGNORECASE flag for am/pm
        self.compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.PATTERNS]
    
    def parse_file(self, file_path: str, preferred_user: Optional[str] = None) -> Dict:
        """
        Parse a WhatsApp chat export file.
        
        Args:
            file_path: Path to the WhatsApp chat export .txt file
            preferred_user: Optional name of the support agent to prioritize
            
        Returns:
            Dictionary containing:
                - messages: List of parsed messages
                - stats: Statistics about the chat
                - primary_user: Identified primary user (support agent)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            # Try with different encoding
            with open(file_path, 'r', encoding='latin-1') as f:
                lines = f.readlines()
        
        messages = self._parse_lines(lines)
        primary_user = self._identify_primary_user(messages, preferred_user)
        stats = self._calculate_stats(messages, primary_user)
        
        return {
            'messages': messages,
            'primary_user': primary_user,
            'stats': stats
        }
    
    def parse_content(self, content: str) -> Dict:
        """
        Parse WhatsApp chat content from a string.
        
        Args:
            content: Raw WhatsApp chat export content
            
        Returns:
            Same as parse_file()
        """
        lines = content.split('\n')
        messages = self._parse_lines(lines)
        primary_user = self._identify_primary_user(messages)
        stats = self._calculate_stats(messages, primary_user)
        
        return {
            'messages': messages,
            'primary_user': primary_user,
            'stats': stats
        }
    
    def _parse_lines(self, lines: List[str]) -> List[Dict]:
        """Parse individual lines into structured messages."""
        messages = []
        current_message = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Try to match against known patterns
            matched = False
            for pattern in self.compiled_patterns:
                match = pattern.match(line)
                if match:
                    # New message found
                    if current_message:
                        messages.append(current_message)
                    
                    timestamp_str, sender, text = match.groups()
                    current_message = {
                        'timestamp': timestamp_str,
                        'sender': sender.strip(),
                        'text': text.strip(),
                        'is_multiline': False
                    }
                    matched = True
                    break
            
            if not matched and current_message:
                # Continuation of previous message (multiline)
                current_message['text'] += f"\n{line}"
                current_message['is_multiline'] = True
        
        # Add the last message
        if current_message:
            messages.append(current_message)
        
        logger.info(f"Parsed {len(messages)} messages from WhatsApp chat")
        return messages
    
    def _identify_primary_user(self, messages: List[Dict], preferred_user: Optional[str] = None) -> Optional[str]:
        """
        Identify the primary user (support agent/creator) based on message patterns.
        
        For customer support chats, the agent typically:
        - Sends longer, more detailed messages
        - Uses more formal/professional language
        - Provides helpful information
        
        Args:
            messages: List of parsed messages
            preferred_user: Optional user name to prefer (from user input)
            
        Returns the identified support agent/creator name.
        """
        if not messages:
            return None
        
        # If user specified a preferred name, try to match it
        if preferred_user:
            senders = set(msg['sender'] for msg in messages)
            for sender in senders:
                if preferred_user.lower() in sender.lower():
                    logger.info(f"Using user-specified primary user: {sender}")
                    return sender
        
        sender_counts = Counter(msg['sender'] for msg in messages)
        
        # If only 2 people, use smarter detection
        if len(sender_counts) == 2:
            # For 2-person chats, pick the one with LONGER average messages
            # Support agents typically write more detailed, helpful responses
            senders = list(sender_counts.keys())
            
            avg_lengths = {}
            for sender in senders:
                sender_msgs = [msg['text'] for msg in messages if msg['sender'] == sender]
                avg_len = sum(len(m) for m in sender_msgs) / len(sender_msgs) if sender_msgs else 0
                avg_lengths[sender] = avg_len
            
            # Pick sender with longer average message length
            primary_user = max(avg_lengths, key=avg_lengths.get)
            logger.info(f"Identified primary user by message length: {primary_user} (avg {avg_lengths[primary_user]:.0f} chars)")
            return primary_user
        
        # For group chats or fallback: use most common sender
        if sender_counts:
            primary_user, count = sender_counts.most_common(1)[0]
            logger.info(f"Identified primary user by frequency: {primary_user} ({count} messages)")
            return primary_user
        
        return None
    
    def _calculate_stats(self, messages: List[Dict], primary_user: Optional[str]) -> Dict:
        """Calculate statistics about the chat."""
        if not messages:
            return {
                'total_messages': 0,
                'primary_user_messages': 0,
                'other_users_messages': 0,
                'unique_senders': 0,
                'date_range': None
            }
        
        primary_count = sum(1 for msg in messages if msg['sender'] == primary_user)
        unique_senders = len(set(msg['sender'] for msg in messages))
        
        return {
            'total_messages': len(messages),
            'primary_user_messages': primary_count,
            'other_users_messages': len(messages) - primary_count,
            'unique_senders': unique_senders,
            'date_range': f"{messages[0]['timestamp']} - {messages[-1]['timestamp']}"
        }
    
    def chunk_messages(
        self, 
        messages: List[Dict], 
        chunk_size: int = 30,
        overlap: int = 5
    ) -> List[Dict]:
        """
        Group messages into conversation blocks for embedding.
        
        Args:
            messages: List of parsed messages
            chunk_size: Number of messages per chunk
            overlap: Number of messages to overlap between chunks
            
        Returns:
            List of conversation block dictionaries
        """
        if not messages:
            return []
        
        chunks = []
        i = 0
        
        while i < len(messages):
            chunk_end = min(i + chunk_size, len(messages))
            chunk_messages = messages[i:chunk_end]
            
            chunks.append({
                'messages': chunk_messages,
                'start_index': i,
                'end_index': chunk_end - 1,
                'message_count': len(chunk_messages),
                'date_range': f"{chunk_messages[0]['timestamp']} - {chunk_messages[-1]['timestamp']}",
                'senders': list(set(msg['sender'] for msg in chunk_messages))
            })
            
            # Move forward with overlap
            i += (chunk_size - overlap)
        
        logger.info(f"Created {len(chunks)} conversation blocks from {len(messages)} messages")
        return chunks
    
    def format_chunk_for_embedding(self, chunk: Dict) -> str:
        """
        Format a conversation chunk into text suitable for embedding.
        
        Args:
            chunk: Chunk dictionary from chunk_messages()
            
        Returns:
            Formatted text representation of the conversation
        """
        formatted_lines = [
            f"Conversation ({chunk['date_range']}):",
            ""
        ]
        
        for msg in chunk['messages']:
            formatted_lines.append(f"{msg['sender']}: {msg['text']}")
        
        return "\n".join(formatted_lines)
    
    def extract_primary_user_messages(
        self, 
        messages: List[Dict], 
        primary_user: str
    ) -> List[Dict]:
        """
        Extract only messages from the primary user.
        Useful for analyzing speaking style.
        
        Args:
            messages: List of all messages
            primary_user: Name of the primary user
            
        Returns:
            List of messages from primary user only
        """
        primary_messages = [
            msg for msg in messages 
            if msg['sender'] == primary_user
        ]
        
        logger.info(f"Extracted {len(primary_messages)} messages from primary user: {primary_user}")
        return primary_messages


def parse_whatsapp_file(file_path: str) -> Dict:
    """
    Convenience function to parse a WhatsApp chat file.
    
    Args:
        file_path: Path to WhatsApp export file
        
    Returns:
        Parsed chat data dictionary
    """
    parser = WhatsAppParser()
    return parser.parse_file(file_path)


def parse_whatsapp_content(content: str) -> Dict:
    """
    Convenience function to parse WhatsApp chat content.
    
    Args:
        content: Raw WhatsApp chat export text
        
    Returns:
        Parsed chat data dictionary
    """
    parser = WhatsAppParser()
    return parser.parse_content(content)


# Example usage
if __name__ == "__main__":
    # Test the parser
    sample_chat = """
12/31/23, 10:30 PM - John: Hey, how are you?
12/31/23, 10:31 PM - Alice: I'm good! How about you?
12/31/23, 10:32 PM - John: Doing great! Working on my new project.
It's going to be amazing.
12/31/23, 10:33 PM - Alice: That's awesome! Tell me more
12/31/23, 10:35 PM - John: It's a chatbot platform that learns from your content
    """
    
    result = parse_whatsapp_content(sample_chat)
    
    print(f"Total messages: {result['stats']['total_messages']}")
    print(f"Primary user: {result['primary_user']}")
    print(f"\nFirst message:")
    print(f"  Sender: {result['messages'][0]['sender']}")
    print(f"  Text: {result['messages'][0]['text']}")
