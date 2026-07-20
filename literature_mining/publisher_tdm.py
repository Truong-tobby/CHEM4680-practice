# Overview
# publisher_tdm.py is the full-text download layer. After doi_discovery finds
# DOI + publisher, this module:
#
# - Downloads papers (XML or PDF) into raw/...
# - Downloads figures (Elsevier Object API / direct URLs)
# - Mitigates WAF/Cloudflare via browser impersonation (curl_cffi + Chrome
#   headers + cookie priming)
# Short form: input = DOI + publisher → output = local path (.xml / .pdf)
# for parsers.py.
#
# Pipeline position:
#   doi_discovery → publisher_tdm (download) → parsers → chunking → RAG
#
# Class PublisherTDMDownloader:
# Initialize with API keys and inter-request delay:
#
# downloader = PublisherTDMDownloader(
#     api_keys={
#         "elsevier": "YOUR_ELS_KEY",
#         "wiley": "YOUR_WILEY_TOKEN",
#         "springer": "YOUR_SPRINGER_KEY",  # or "spring nature"
#     },
#     request_interval=3.0,  # sleep 3s after each GET (avoid rate limits)
#     timeout=180.0,
# )









import logging
import os
import time
import random
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

try:
    # curl_cffi provides browser-like TLS fingerprints and can bypass
    # many WAF/Cloudflare protections when using browser impersonation.
    from curl_cffi import requests as cffi_requests  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cffi_requests = None

from .config import USER_AGENT


logger = logging.getLogger(__name__)


class PublisherTDMDownloader:
    """
    High-fidelity downloader for publisher TDM and OA full text.

    - Uses curl_cffi with Chrome impersonation when available.
    - Injects browser-like Sec-CH and fetch headers.
    - Adds Referer=https://doi.org/{doi} for OA / Crossref TDM.
    - Implements a simple 403 retry with cooldown and cookie reset.
    """

    def __init__(
        self,
        api_keys: Dict[str, str],
        request_interval: float = 3.0,
        timeout: float = 180.0,
    ) -> None:
        self.api_keys = api_keys
        self.request_interval = request_interval
        self.timeout = timeout
        # Set by download_* helpers: missing_api_key | api_forbidden | http_error | None
        self.last_error: Optional[str] = None

        if cffi_requests is not None:
            self.session = cffi_requests.Session()
            # Use recent Chrome profiles; combined with manual headers this
            # closely mimics a modern browser.
            self.browser_profiles = ["chrome120", "chrome124"]
            self.current_profile = "chrome120"
            logger.info(
                "Initialized PublisherTDMDownloader with curl_cffi profile: %s",
                self.current_profile,
            )
        else:
            # Fallback: plain requests Session (less robust against WAF).
            self.session = requests.Session()
            self.browser_profiles: List[str] = []
            self.current_profile = None
            logger.warning("curl_cffi not available; falling back to requests.Session()")

    @staticmethod
    def _ensure_dir(path: str) -> None:
        clean_path = path.strip()
        os.makedirs(clean_path, exist_ok=True)

# _sanitize_doi(doi): turn a DOI into a safe filename.
#   Input:  10.1016/j.jpowsour.2020.228123
#   Output: 10.1016_j.jpowsour.2020.228123
    @staticmethod
    def _sanitize_doi(doi: str) -> str:
        return doi.replace("/", "_").replace(":", "_")

# _get_browser_headers(referer): build Chrome-like headers
# (User-Agent, Sec-CH-UA, Sec-Fetch-*, Referer, ...).
    def _get_browser_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        """
        Build a Chrome-like header set, including Sec-CH-* and fetch headers.
        """
        headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "max-age=0",
            "Sec-Ch-Ua": (
                '"Not_A Brand";v="8", "Chromium";v="120", '
                '"Google Chrome";v="120"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        else:
            headers["Sec-Fetch-Site"] = "none"
        return headers
# Unified GET: attach browser headers → on 403, sleep 5–8s,
# clear cookies, retry once → always sleep request_interval afterward.
    def _get(
        self,
        url: str,
        extra_headers: Optional[Dict[str, str]] = None,
        retry: bool = True,
    ) -> Optional[requests.Response]:
        """
        Unified GET with high-fidelity headers and simple 403 retry.
        """
        extra_headers = extra_headers or {}

        referer = extra_headers.get("Referer")
        if not referer:
            parsed = urlparse(url)
            referer = f"{parsed.scheme}://{parsed.netloc}/"

        final_headers = self._get_browser_headers(referer)
        final_headers.update(extra_headers)
# using session instead of requests.get because:
# maintain cookies for later requests
        try:
            if cffi_requests is not None and isinstance(
                self.session, cffi_requests.Session
            ):
                resp = self.session.get(
                    url,
                    headers=final_headers,
                    timeout=self.timeout,
                    impersonate=self.current_profile,
                    allow_redirects=True,
                )
            else:
                resp = self.session.get(
                    url,
                    headers=final_headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )

            if resp.status_code == 200:
                return resp

            if resp.status_code == 403 and retry:
                logger.warning(
                    "GET %s blocked with 403; cooling down and retrying once...", url
                )
                time.sleep(random.uniform(5.0, 8.0))
                try:
                    self.session.cookies.clear()
                except Exception:
                    pass
                return self._get(url, extra_headers=extra_headers, retry=False)

            logger.warning("GET %s failed: %s", url, resp.status_code)
            return None
        except Exception as exc:
            logger.error("Error requesting %s: %s", url, exc)
            return None
        finally:
            time.sleep(self.request_interval)
# "Knock" on the publisher homepage before downloading PDFs
# (ACS, PNAS, MDPI, ...). Example: publisher_key="acs" →
# GET https://pubs.acs.org to obtain a session cookie.
# _prime_cookies(publisher_key)

    def _prime_cookies(self, publisher_key: str) -> None:
        """
        Lightweight "knock" on the publisher homepage to prime cookies.
        """
        home_urls = {
            "acs": "https://pubs.acs.org",
            "pnas": "https://www.pnas.org",
            "aaas": "https://www.science.org",
            "hindawi": "https://www.hindawi.com",
            "rsc": "https://pubs.rsc.org",
            "mdpi": "https://www.mdpi.com",
        }
        target = home_urls.get(publisher_key)
        if not target:
            return

        logger.info("Priming cookies via homepage: %s", target)
        try:
            self._get(target, retry=False)
            time.sleep(random.uniform(2.0, 4.0))
        except Exception:
            # Priming is best-effort; failures are non-fatal.
            pass

    # ------------------------------------------------------------------
    # Publisher-specific downloaders
    # ------------------------------------------------------------------

    def download_elsevier_xml(
        self, doi: str, out_dir: str = "raw/elsevier/xml"
    ) -> Optional[str]:
        """
        Download Elsevier full text as XML via the official TDM API.
        Sets self.last_error on failure (missing_api_key | api_forbidden | http_error).
        """
        self.last_error = None
        api_key = self.api_keys.get("elsevier")
        if not api_key:
            self.last_error = "missing_api_key"
            logger.error("Elsevier API key not found in api_keys (missing_api_key).")
            return None

        self._ensure_dir(out_dir)
        safe_doi = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_doi}.xml")
        if os.path.exists(out_path):
            logger.info("Elsevier XML already exists for %s -> %s", doi, out_path)
            return out_path

        url = f"https://api.elsevier.com/content/article/doi/{doi}"
        headers = {
            "User-Agent": USER_AGENT,
            "X-ELS-APIKey": api_key,
            "Accept": "text/xml",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                logger.info("Saved Elsevier XML for %s -> %s", doi, out_path)
                return out_path
            if resp.status_code in (401, 403):
                self.last_error = "api_forbidden"
                logger.warning(
                    "Elsevier API forbidden (%s) for %s — check TDM entitlement",
                    resp.status_code,
                    doi,
                )
            else:
                self.last_error = "http_error"
                logger.warning(
                    "Elsevier API returned %s for %s", resp.status_code, url
                )
        except Exception as exc:
            self.last_error = "http_error"
            logger.error("Elsevier API error for %s: %s", doi, exc)
        return None
# 1. download_elsevier_xml(doi) — official Elsevier TDM API
# Input:
#   doi = "10.1016/j.electacta.2021.139012"
# Flow:
#   GET https://api.elsevier.com/content/article/doi/{doi}
#   Headers: X-ELS-APIKey, Accept: text/xml
# Output (success):
#   raw/elsevier/xml/10.1016_j.electacta.2021.139012.xml
# If the file already exists → return path without re-downloading.
# Missing key / API error → return None.
    def download_springer_xml(
        self, doi: str, out_dir: str = "raw/springer/xml"
    ) -> Optional[str]:
        """
        Download Springer XML/metadata via Springer Nature meta API.
        """
        api_key = self.api_keys.get("spring nature") or self.api_keys.get("springer")
        if not api_key:
            logger.error("Springer API key not found in api_keys.")
            return None

        self._ensure_dir(out_dir)
        safe_doi = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_doi}.xml")
        if os.path.exists(out_path):
            logger.info("Springer XML already exists for %s -> %s", doi, out_path)
            return out_path

        url = f"https://api.springernature.com/meta/v2/pam?q=doi:{doi}&api_key={api_key}"
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if resp.status_code == 200:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                logger.info("Saved Springer XML/meta for %s -> %s", doi, out_path)
                return out_path
            logger.warning(
                "Springer API returned %s for %s", resp.status_code, url
            )
        except Exception as exc:
            logger.error("Springer API error for %s: %s", doi, exc)
        return None
# 2. download_springer_xml(doi) — Meta API
# Input: doi = "10.1007/s10008-020-04567-8"
# Output: raw/springer/xml/10.1007_s10008-020-04567-8.xml
# Note: usually metadata/PAM, not full-text body. In download_fulltext,
# Springer actually prefers PDF via Crossref.
    def download_wiley_pdf(
        self, doi: str, out_dir: str = "raw/wiley/pdf"
    ) -> Optional[str]:
        """
        Download Wiley full text as PDF via Wiley TDM interface.
        Sets self.last_error on failure (missing_api_key | api_forbidden | http_error).
        """
        self.last_error = None
        token = self.api_keys.get("wiley")
        if not token:
            self.last_error = "missing_api_key"
            logger.error(
                "Wiley TDM token not found in api_keys (key 'wiley') — missing_api_key."
            )
            return None

        self._ensure_dir(out_dir)
        safe_name = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_name}.pdf")
        if os.path.exists(out_path):
            logger.info("Wiley PDF already exists for %s -> %s", doi, out_path)
            return out_path

        import urllib.parse

        encoded_doi = urllib.parse.quote(doi, safe="")
        url = f"http://api.wiley.com/onlinelibrary/tdm/v1/articles/{encoded_doi}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf",
            "Wiley-TDM-Client-Token": token,
        }

        try:
            resp = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
            if resp.status_code == 200:
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                logger.info("Saved Wiley PDF for %s -> %s", doi, out_path)
                return out_path
            if resp.status_code in (401, 403):
                self.last_error = "api_forbidden"
                logger.warning(
                    "Wiley TDM forbidden (%s) for %s — check TDM token/entitlement",
                    resp.status_code,
                    doi,
                )
            else:
                self.last_error = "http_error"
                logger.warning("Wiley TDM returned %s for %s", resp.status_code, url)
        except Exception as exc:
            self.last_error = "http_error"
            logger.error("Wiley TDM error for %s: %s", doi, exc)
        return None
# 3. download_wiley_pdf(doi) — TDM token
# Input:
#   doi = "10.1002/aenm.202100234"
# Flow:
#   GET http://api.wiley.com/onlinelibrary/tdm/v1/articles/{encoded_doi}
#   Headers: Wiley-TDM-Client-Token, Accept: application/pdf
# Output:
#   raw/wiley/pdf/10.1002_aenm.202100234.pdf
    # ------------------------------------------------------------------
    # Image Download Helpers (Elsevier & Generic)
    # ------------------------------------------------------------------

    def download_image_generic(self, url: str, out_path: str) -> bool:
        """
        Generic image downloader for Springer or direct URLs.
        """
        if os.path.exists(out_path):
            return True

        try:
            resp = self._get(url, extra_headers={"Accept": "image/*"})
            if resp and resp.status_code == 200:
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                logger.debug("Downloaded image generic: %s -> %s", url, out_path)
                return True
        except Exception as exc:
            logger.error("Failed to download image %s: %s", url, exc)
        return False

    def download_elsevier_image(self, doi: str, image_ref: str, out_path: str) -> bool:
        """
        Download an image object from Elsevier using the Object Retrieval API.

        image_ref: The internal reference ID from the XML (e.g., 'gr1').
        """
        if os.path.exists(out_path):
            return True

        api_key = self.api_keys.get("elsevier")
        if not api_key:
            return False

        # Elsevier Object API format:
        #   https://api.elsevier.com/content/object/doi/{doi}/{ref}
        url = f"https://api.elsevier.com/content/object/doi/{doi}/{image_ref}"

        headers = {
            "X-ELS-APIKey": api_key,
            "Accept": "image/jpeg,image/png,image/*",
            "User-Agent": USER_AGENT,
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                logger.debug("Downloaded Elsevier image: %s -> %s", image_ref, out_path)
                return True
            logger.warning("Elsevier Image API %s returned %s", url, resp.status_code)
        except Exception as exc:
            logger.error("Elsevier Image download error for %s/%s: %s", doi, image_ref, exc)
        return False

    # ------------------------------------------------------------------
    # Generic Crossref-based downloader
    # ------------------------------------------------------------------

    def download_via_crossref_tdm(
        self,
        doi: str,
        out_dir: str = "raw/other/pdf",
        preferred_hosts: Optional[List[str]] = None,
        publisher_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Locate TDM links via Crossref and download PDF with browser-like headers.
        """
        self._ensure_dir(out_dir)
        safe_name = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_name}.pdf")
        if os.path.exists(out_path):
            return out_path

        if publisher_key:
            self._prime_cookies(publisher_key)

        import urllib.parse

        encoded_doi = urllib.parse.quote(doi)
        url = f"https://api.crossref.org/works/{encoded_doi}"
        try:
            raw_resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if raw_resp.status_code != 200:
                logger.warning("Crossref API returned %s for %s", raw_resp.status_code, url)
                return None
            message = raw_resp.json().get("message", {})
            links = message.get("link", []) or []
        except Exception as exc:
            logger.error("Crossref API error for %s: %s", doi, exc)
            return None

        if not links:
            logger.warning("No TDM links found in Crossref for %s", doi)
            return None

        def score_link(link: Dict[str, str]) -> int:
            score = 0
            app = (link.get("intended-application") or "").lower()
            ctype = (link.get("content-type") or "").lower()
            url_l = (link.get("URL") or link.get("url") or "").lower()
            if app == "text-mining":
                score += 3
            if "pdf" in ctype:
                score += 2
            if preferred_hosts and any(h in url_l for h in preferred_hosts):
                score += 2
            return score

        best = sorted(links, key=score_link, reverse=True)[0]
        target_url = best.get("URL") or best.get("url")
        if not target_url:
            logger.warning("Best Crossref link for %s lacks URL", doi)
            return None

        logger.info("Downloading PDF via Crossref TDM for %s -> %s", doi, target_url)

        pdf_resp = self._get(
            target_url, extra_headers={"Referer": f"https://doi.org/{doi}"}
        )
        if pdf_resp:
            with open(out_path, "wb") as f:
                f.write(pdf_resp.content)
            logger.info("Saved Crossref-based PDF for %s -> %s", doi, out_path)
            return out_path

        return None
# 4. download_via_crossref_tdm(doi, out_dir, preferred_hosts, publisher_key)
# Used for RSC, ACS, AAAS, PNAS, and "other".
#
# Example input (ACS):
# download_via_crossref_tdm(
#     doi="10.1021/acsenergylett.1c01234",
#     out_dir="raw/acs/pdf",
#     preferred_hosts=["acs.org", "pubs.acs.org"],
#     publisher_key="acs",
# )
# Flow:
# - If publisher_key is set, prime homepage cookies
# - Call Crossref: https://api.crossref.org/works/{doi}
# - Score each message.link:
#     intended-application == text-mining → +3
#     content-type contains pdf → +2
#     URL matches preferred_hosts → +2
# - Pick highest-scoring link → _get with Referer: https://doi.org/{doi}
# - Write PDF to disk
    def download_elsevier_pdf_via_crossref(
        self, doi: str, out_dir: str = "raw/elsevier/pdf"
    ) -> Optional[str]:
        """
        Convenience wrapper: try to obtain an Elsevier PDF via Crossref TDM.
        """
        preferred_hosts = ["elsevier.com", "sciencedirect.com"]
        return self.download_via_crossref_tdm(
            doi, out_dir=out_dir, preferred_hosts=preferred_hosts, publisher_key=None
        )

    def download_springer_pdf_via_crossref(
        self, doi: str, out_dir: str = "raw/springer/pdf"
    ) -> Optional[str]:
        """
        Convenience wrapper: try to obtain a Springer PDF via Crossref TDM.
        """
        preferred_hosts = ["springer.com", "springernature.com", "link.springer.com"]
        return self.download_via_crossref_tdm(
            doi, out_dir=out_dir, preferred_hosts=preferred_hosts, publisher_key=None
        )

    # ------------------------------------------------------------------
    # OpenAlex-based OA downloader (MDPI / Hindawi and other OA)
    # ------------------------------------------------------------------

    def download_pdf_from_url(
        self, doi: str, pdf_url: str, out_dir: str = "raw/oa/pdf"
    ) -> Optional[str]:
        """
        Download a PDF from a known direct URL (e.g. OpenAlex pdf_url from discovery CSV).
        """
        if not pdf_url:
            return None

        self._ensure_dir(out_dir)
        safe_name = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_name}.pdf")
        if os.path.exists(out_path):
            return out_path

        logger.info("Downloading OA PDF for %s -> %s", doi, pdf_url)
        if "mdpi.com" in pdf_url.lower():
            self._prime_cookies("mdpi")

        pdf_resp = self._get(
            pdf_url, extra_headers={"Referer": f"https://doi.org/{doi}"}
        )
        if pdf_resp:
            with open(out_path, "wb") as f:
                f.write(pdf_resp.content)
            logger.info("Saved OA PDF for %s -> %s", doi, out_path)
            return out_path
        return None

    def download_via_openalex_oa(self, doi: str, out_dir: str) -> Optional[str]:
        """
        Download OA PDF by asking OpenAlex for best_oa_location / primary_location.
        """
        self._ensure_dir(out_dir)
        safe_name = self._sanitize_doi(doi)
        out_path = os.path.join(out_dir, f"{safe_name}.pdf")
        if os.path.exists(out_path):
            return out_path

        import urllib.parse

        encoded_doi = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
        oa_url = f"https://api.openalex.org/works/{encoded_doi}"

        try:
            resp = requests.get(oa_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resp.status_code != 200:
                logger.warning(
                    "OpenAlex API returned %s for %s", resp.status_code, oa_url
                )
                return None
            data = resp.json()
            best_oa = data.get("best_oa_location") or {}
            pdf_url = best_oa.get("pdf_url") or data.get(
                "primary_location", {}
            ).get("pdf_url")
        except Exception as exc:
            logger.error("OpenAlex API error for %s: %s", doi, exc)
            return None

        if not pdf_url:
            logger.warning("No OA pdf_url found in OpenAlex for %s", doi)
            return None

        logger.info("OpenAlex OA URL for %s -> %s", doi, pdf_url)

        is_mdpi = "mdpi.com" in pdf_url or "mdpi" in out_dir
        if is_mdpi:
            self._prime_cookies("mdpi")

        pdf_resp = self._get(
            pdf_url, extra_headers={"Referer": f"https://doi.org/{doi}"}
        )
        if pdf_resp:
            with open(out_path, "wb") as f:
                f.write(pdf_resp.content)
            logger.info("Saved OA PDF for %s -> %s", doi, out_path)
            return out_path

        return None
# 5. download_via_openalex_oa(doi, out_dir) — free OA PDFs
# For MDPI / Hindawi (and other OA).
#
# Input:
#   download_via_openalex_oa("10.3390/batteries8010001", out_dir="raw/mdpi/pdf")
# Flow:
#   GET https://api.openalex.org/works/https://doi.org/{doi}
#   Read best_oa_location.pdf_url (or primary_location.pdf_url)
#   If MDPI → prime cookies
#   _get(pdf_url) with Referer doi.org
# Output:
#   raw/mdpi/pdf/10.3390_batteries8010001.pdf

    # ------------------------------------------------------------------
    # Routing helper
    # ------------------------------------------------------------------

    def download_fulltext(
        self, doi: str, publisher: str, journal: Optional[str] = None
    ) -> Optional[str]:
        """
        High-level helper to download full text for a DOI based on publisher.
        """
        pub = (publisher or "").lower().strip()
        jname = (journal or "").lower().strip()

        # 1) Official publisher APIs
        if "elsevier" in pub:
            return self.download_elsevier_xml(doi)
        if "springer" in pub or "springer nature" in pub or jname.startswith("nature "):
            # Springer PAM XML is often metadata-only; prefer PDF via Crossref
            # so downstream parsing matches Wiley / RSC / ACS behavior.
            return self.download_springer_pdf_via_crossref(doi, out_dir="raw/springer/pdf")
        if "wiley" in pub:
            return self.download_wiley_pdf(doi)

        # 2) Open access via OpenAlex (MDPI / Hindawi)
        if "multidisciplinary digital publishing institute" in pub or "mdpi" in pub:
            return self.download_via_openalex_oa(doi, out_dir="raw/mdpi/pdf")
        if "hindawi" in pub:
            return self.download_via_openalex_oa(doi, out_dir="raw/hindawi/pdf")

        # 3) Crossref TDM for RSC / ACS / AAAS / PNAS / others
        preferred_hosts: Optional[List[str]] = None
        out_dir = "raw/other/pdf"
        pub_key: Optional[str] = None

        if "royal society of chemistry" in pub or "rsc" in pub:
            preferred_hosts = ["rsc.org", "pubs.rsc.org"]
            out_dir = "raw/rsc/pdf"
            pub_key = "rsc"
        elif "american chemical society" in pub or "acs publications" in pub or jname.startswith(
            "acs "
        ):
            preferred_hosts = ["acs.org", "pubs.acs.org"]
            out_dir = "raw/acs/pdf"
            pub_key = "acs"
        elif (
            "american association for the advancement of science" in pub
            or "aaas" in pub
            or jname == "science"
        ):
            preferred_hosts = ["science.org", "sciencemag.org"]
            out_dir = "raw/aaas/pdf"
            pub_key = "aaas"
        elif (
            "national academy of sciences" in pub
            or "pnas" in pub
            or "proceedings of the national academy of sciences" in jname
        ):
            preferred_hosts = ["pnas.org", "nas.org"]
            out_dir = "raw/pnas/pdf"
            pub_key = "pnas"

        # Extra slowdown for ACS, which is particularly sensitive.
        if pub_key == "acs":
            logger.info("Extra cooldown before ACS download...")
            time.sleep(random.uniform(5.0, 10.0))

        return self.download_via_crossref_tdm(
            doi,
            out_dir=out_dir,
            preferred_hosts=preferred_hosts,
            publisher_key=pub_key,
        )
# download_fulltext routing summary
# Publisher / journal condition → method → output dir / format
#
# contains "elsevier"
#   → download_elsevier_xml → raw/elsevier/xml/*.xml
#
# springer / springer nature / journal nature ...
#   → download_springer_pdf_via_crossref → raw/springer/pdf/*.pdf
#
# contains "wiley"
#   → download_wiley_pdf → raw/wiley/pdf/*.pdf
#
# mdpi / Multidisciplinary...
#   → download_via_openalex_oa → raw/mdpi/pdf/*.pdf
#
# hindawi
#   → download_via_openalex_oa → raw/hindawi/pdf/*.pdf
#
# RSC
#   → Crossref TDM + prime rsc → raw/rsc/pdf/*.pdf
#
# ACS
#   → Crossref + extra 5–10s cooldown → raw/acs/pdf/*.pdf
#
# AAAS / journal Science
#   → Crossref → raw/aaas/pdf/*.pdf
#
# PNAS
#   → Crossref → raw/pnas/pdf/*.pdf
#
# other
#   → Crossref generic → raw/other/pdf/*.pdf
