"""Tornado endpoint for querying an EVM address risk profile."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from dotenv import load_dotenv
import tornado.httpclient
import tornado.web

load_dotenv()

_EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
_UNAVAILABLE_MESSAGE = "Intercepta screening unavailable"
logger = logging.getLogger(__name__)


def _fail_closed():
    return os.getenv("INTERCEPTA_FAIL_CLOSED", "false").strip().lower() in ("true", "1")


def _risk_endpoint(base_url, address, chain_id):
    base_url = base_url.rstrip("/")
    if "{address}" in base_url:
        endpoint = base_url.format(address=address)
    elif "/extension/account" in base_url:
        endpoint = f"{base_url}/{address}/toxic-score"
    elif base_url.endswith("/v1") or base_url.endswith("/v2"):
        root = re.sub(r"/v[12]$", "", base_url)
        endpoint = f"{root}/api/public/v2/extension/account/{address}/toxic-score"
    elif "api.web3antivirus.io" in base_url:
        endpoint = f"{base_url}/api/public/v2/extension/account/{address}/toxic-score"
    else:
        endpoint = f"{base_url}/api/public/v2/extension/account/{address}/toxic-score"
    return endpoint + "?" + urlencode({"chain_id": chain_id})


class RiskProfileHandler(tornado.web.RequestHandler):
    def _finish_mock_fallback(self, address, chain_id=1):
        self.finish({
            "address": address,
            "chainId": chain_id,
            "verdict": "PASS",
            "riskScore": 0,
            "reasons": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "mock_fallback",
        })

    async def get(self, address):
        if not _EVM_ADDRESS.fullmatch(address):
            self.set_status(400)
            self.finish({"error": "Invalid EVM address", "code": "INVALID_ADDRESS"})
            return

        raw_chain_id = self.get_query_argument("chainId", "1")
        try:
            chain_id = int(raw_chain_id)
            if chain_id <= 0:
                raise ValueError("chainId must be positive")
        except (TypeError, ValueError):
            self.set_status(400)
            self.finish({"error": "Invalid chainId", "code": "INVALID_CHAIN_ID"})
            return

        base_url = os.environ.get(
            "INTERCEPTA_BASE_URL", "https://api.web3antivirus.io/v1"
        ).strip()
        api_key = os.environ.get("INTERCEPTA_API_KEY", "")

        request = None
        try:
            timeout_ms = int(os.environ.get("INTERCEPTA_TIMEOUT_MS", "2500"))
            if timeout_ms <= 0:
                raise ValueError("INTERCEPTA_TIMEOUT_MS must be positive")
            request = tornado.httpclient.HTTPRequest(
                url=_risk_endpoint(base_url, address, chain_id),
                method="GET",
                headers={
                    "Accept": "application/json",
                    "X-API-KEY": api_key,
                },
                request_timeout=timeout_ms / 1000.0,
                connect_timeout=timeout_ms / 1000.0,
            )
            logger.info("Sending Intercepta request to: %s", request.url)
            response = await tornado.httpclient.AsyncHTTPClient().fetch(request)
            result = json.loads(response.body.decode("utf-8"))
            if isinstance(result.get("data"), dict):
                result = result["data"]

            raw_score = (
                result.get("toxicScore")
                if result.get("toxicScore") is not None
                else result.get("risk_score", result.get("riskScore", result.get("score", 0)))
            )
            risk_score = float(raw_score)
            if not (risk_score >= 0):
                raise ValueError("Intercepta returned an invalid risk score")

            raw_reasons = (
                result.get("traits")
                if result.get("traits") is not None
                else result.get("reasons", result.get("flags", []))
            )
            reasons = []
            if isinstance(raw_reasons, list):
                for item in raw_reasons:
                    if isinstance(item, dict):
                        reason_name = item.get("name") or item.get("description") or item.get("code")
                        if reason_name:
                            reasons.append(str(reason_name))
                    elif isinstance(item, str):
                        reasons.append(item)

            self.finish({
                "address": address,
                "chainId": chain_id,
                "verdict": "BLOCK" if risk_score >= 70 else "PASS",
                "riskScore": risk_score,
                "reasons": reasons,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except tornado.httpclient.HTTPClientError as exc:
            req_url = request.url if request else base_url
            if not _fail_closed():
                logger.warning(
                    "%s; HTTP %s from %s, using mock fallback: %s",
                    _UNAVAILABLE_MESSAGE,
                    exc.code,
                    req_url,
                    exc,
                )
                self._finish_mock_fallback(address, chain_id)
                return

            logger.exception(
                "%s: HTTP %s from %s",
                _UNAVAILABLE_MESSAGE,
                exc.code,
                req_url,
            )
            self.set_status(500)
            self.finish({
                "error": _UNAVAILABLE_MESSAGE,
                "code": "RISK_PROFILE_UNAVAILABLE",
            })
            return
        except Exception as exc:
            if _fail_closed():
                logger.exception("%s: %s", _UNAVAILABLE_MESSAGE, exc)
                self.set_status(500)
                self.finish({
                    "error": _UNAVAILABLE_MESSAGE,
                    "code": "RISK_PROFILE_UNAVAILABLE",
                })
                return

            logger.warning("%s; using mock fallback: %s", _UNAVAILABLE_MESSAGE, exc)
            self._finish_mock_fallback(address, chain_id)
