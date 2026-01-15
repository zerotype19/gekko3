"""
Discord Notifier
Lightweight Discord Webhook client for system notifications
Fire-and-forget design to avoid blocking the trading loop
"""

import aiohttp
import asyncio
import logging
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Color constants for Discord embeds
COLOR_GREEN = 0x00FF00   # Startup, Signal, Success
COLOR_RED = 0xFF0000     # Error, Shutdown, Critical
COLOR_YELLOW = 0xFFFF00  # Warning, Neutral, Info
COLOR_BLUE = 0x0099FF    # Info, Trend Change, Status


class DiscordNotifier:
    """Discord Webhook notifier - fire and forget async notifications"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        Initialize Discord Notifier
        
        Args:
            webhook_url: Discord webhook URL (if None, loads from DISCORD_WEBHOOK_URL env var)
        """
        self.webhook_url = webhook_url or os.getenv('DISCORD_WEBHOOK_URL', '')
        self.enabled = bool(self.webhook_url)
        
        if not self.enabled:
            logging.warning("⚠️  Discord notifications disabled (DISCORD_WEBHOOK_URL not set)")
        else:
            logging.info("✅ Discord Notifier initialized")
    
    async def send(self, message: str, color: int = COLOR_BLUE, title: Optional[str] = None, fields: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Send a Discord notification (fire and forget)
        
        Args:
            message: Message content
            color: Embed color (use COLOR_* constants)
            title: Optional embed title
            fields: Optional list of field dicts [{'name': '...', 'value': '...', 'inline': True}]
            
        Returns:
            True if sent successfully, False otherwise (does not raise exceptions)
        """
        if not self.enabled:
            return False
        
        # Build embed payload
        embed = {
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()  # Explicit timestamp
        }
        
        if title:
            embed["title"] = title
            
        if fields:
            embed["fields"] = fields
        
        payload = {
            "embeds": [embed]
        }
        
        # Fire and forget - don't block on network calls
        try:
            # Downgraded to DEBUG to reduce log noise
            logging.debug(f"📤 Sending Discord notification: {title or 'Untitled'}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)  # 5 second timeout
                ) as resp:
                    if 200 <= resp.status < 300:
                        logging.debug(f"✅ Discord notification sent: {title or 'Untitled'}")
                        return True
                    else:
                        error_text = await resp.text()
                        logging.warning(f"⚠️  Discord webhook returned {resp.status}: {error_text}")
                        return False
        except asyncio.TimeoutError:
            logging.warning("⚠️  Discord webhook timeout (not blocking)")
            return False
        except Exception as e:
            logging.warning(f"⚠️  Discord notification failed (non-blocking): {e}")
            return False
    
    async def send_info(self, message: str, title: Optional[str] = None) -> bool:
        """Send info notification (blue)"""
        return await self.send(message, COLOR_BLUE, title)
    
    async def send_success(self, message: str, title: Optional[str] = None, fields: Optional[List[Dict]] = None) -> bool:
        """Send success notification (green)"""
        return await self.send(message, COLOR_GREEN, title, fields)
    
    async def send_warning(self, message: str, title: Optional[str] = None) -> bool:
        """Send warning notification (yellow)"""
        return await self.send(message, COLOR_YELLOW, title)
    
    async def send_error(self, message: str, title: Optional[str] = None) -> bool:
        """Send error notification (red)"""
        return await self.send(message, COLOR_RED, title)


# Global notifier instance (initialized on import)
_notifier: Optional[DiscordNotifier] = None


def get_notifier() -> DiscordNotifier:
    """Get or create global notifier instance"""
    global _notifier
    if _notifier is None:
        _notifier = DiscordNotifier()
    return _notifier
