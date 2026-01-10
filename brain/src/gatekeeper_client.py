"""
Gatekeeper Client
Async HTTP client for communicating with the Gekko3 Cloudflare Gatekeeper
Handles proposal signing and submission
"""

import aiohttp
import hmac
import hashlib
import json
import time
import os
import uuid
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class GatekeeperClient:
    """Client for sending signed proposals to the Gekko3 Gatekeeper"""

    def __init__(self, base_url: Optional[str] = None, api_secret: Optional[str] = None):
        """
        Initialize the Gatekeeper client
        
        Args:
            base_url: Gatekeeper URL (defaults to GATEKEEPER_URL env var)
            api_secret: API secret for signing (defaults to API_SECRET env var)
        """
        self.base_url = base_url or os.getenv('GATEKEEPER_URL', '').rstrip('/')
        if not self.base_url:
            raise ValueError('GATEKEEPER_URL must be set in .env or provided to constructor')
        
        self.api_secret = api_secret or os.getenv('API_SECRET', '')
        if not self.api_secret:
            raise ValueError('API_SECRET must be set in .env or provided to constructor')

    def _sign_payload(self, payload_str: str) -> str:
        """
        Create HMAC-SHA256 signature for the payload
        
        Args:
            payload_str: JSON string of the proposal payload
            
        Returns:
            Hex digest of the HMAC signature
        """
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def send_proposal(
        self,
        proposal_dict: Dict[str, Any],
        session: Optional[aiohttp.ClientSession] = None
    ) -> Dict[str, Any]:
        """
        Send a signed trade proposal to the Gatekeeper
        
        Args:
            proposal_dict: Proposal dictionary (will be augmented with id/timestamp if missing)
            session: Optional aiohttp session (creates new one if not provided)
            
        Returns:
            Response dictionary with status and details
            
        Raises:
            aiohttp.ClientError: On network errors
            ValueError: On invalid proposal structure
        """
        # Ensure proposal has required fields
        if 'id' not in proposal_dict:
            proposal_dict['id'] = str(uuid.uuid4())
        
        if 'timestamp' not in proposal_dict:
            proposal_dict['timestamp'] = int(time.time() * 1000)  # milliseconds
        
        # Validate required fields (price is now mandatory per new Gatekeeper requirements)
        required_fields = ['symbol', 'strategy', 'side', 'quantity', 'price', 'legs', 'context', 'signature']
        missing_fields = [field for field in required_fields if field not in proposal_dict]
        if missing_fields and 'signature' not in missing_fields:
            # Signature will be added below, so only check others
            missing_fields = [f for f in missing_fields if f != 'signature']
            if missing_fields:
                raise ValueError(f'Missing required fields: {missing_fields}')
        
        # Validate side is OPEN or CLOSE (not BUY/SELL)
        if proposal_dict.get('side') not in ['OPEN', 'CLOSE']:
            raise ValueError(f"Invalid side: {proposal_dict.get('side')}. Must be 'OPEN' or 'CLOSE'")
        
        # Validate price is positive
        if 'price' in proposal_dict and (proposal_dict['price'] is None or proposal_dict['price'] <= 0):
            raise ValueError(f"Invalid price: {proposal_dict.get('price')}. Price must be positive for limit orders")

        # For signing, we need to create the payload WITHOUT the signature field
        # Then sign it, then add the signature to both payload and header
        proposal_for_signing = proposal_dict.copy()
        proposal_for_signing.pop('signature', None)  # Remove signature if present
        
        # Convert to JSON string for signing (canonical form)
        payload_json = json.dumps(proposal_for_signing, sort_keys=True, separators=(',', ':'))
        
        # Generate signature
        signature = self._sign_payload(payload_json)
        
        # Add signature to the proposal payload
        proposal_dict['signature'] = signature
        
        # Final payload with signature included
        final_payload_json = json.dumps(proposal_dict, sort_keys=True, separators=(',', ':'))

        # Prepare headers
        headers = {
            'Content-Type': 'application/json',
            'X-GW-Signature': signature,
            'X-GW-Timestamp': str(proposal_dict['timestamp']),
        }

        url = f'{self.base_url}/v1/proposal'
        
        # Use provided session or create new one
        use_external_session = session is not None
        if not use_external_session:
            session = aiohttp.ClientSession()

        try:
            async with session.post(url, data=final_payload_json, headers=headers) as response:
                response_data = await response.json()
                
                # Map HTTP status codes to result
                if response.status == 200:
                    return {
                        'status': 'APPROVED',
                        'data': response_data,
                        'http_status': response.status
                    }
                elif response.status == 400:
                    return {
                        'status': 'BAD_REQUEST',
                        'error': response_data.get('error', 'Bad Request'),
                        'http_status': response.status
                    }
                elif response.status == 403:
                    return {
                        'status': 'REJECTED',
                        'reason': response_data.get('reason', 'Proposal rejected'),
                        'data': response_data,
                        'http_status': response.status
                    }
                elif response.status == 401:
                    return {
                        'status': 'UNAUTHORIZED',
                        'error': 'Authentication failed',
                        'http_status': response.status
                    }
                elif response.status == 500:
                    return {
                        'status': 'GATEKEEPER_ERROR',
                        'error': response_data.get('error', 'Internal server error'),
                        'http_status': response.status
                    }
                else:
                    return {
                        'status': 'UNKNOWN_ERROR',
                        'error': f'Unexpected status: {response.status}',
                        'data': response_data,
                        'http_status': response.status
                    }
        finally:
            # Only close session if we created it
            if not use_external_session:
                await session.close()

    async def get_status(self, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
        """
        Get current Gatekeeper system status
        
        Args:
            session: Optional aiohttp session (creates new one if not provided)
            
        Returns:
            Status dictionary with system state, positions, equity, etc.
            
        Raises:
            aiohttp.ClientError: On network errors
        """
        url = f'{self.base_url}/v1/status'
        
        use_external_session = session is not None
        if not use_external_session:
            session = aiohttp.ClientSession()

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'status': 'OK',
                        'data': data,
                        'http_status': response.status
                    }
                elif response.status == 401:
                    return {
                        'status': 'UNAUTHORIZED',
                        'error': 'Authentication required',
                        'http_status': response.status
                    }
                else:
                    error_text = await response.text()
                    return {
                        'status': 'ERROR',
                        'error': error_text,
                        'http_status': response.status
                    }
        finally:
            if not use_external_session:
                await session.close()

