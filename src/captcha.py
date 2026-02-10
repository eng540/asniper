"""
Elite Sniper v2.0 - Hybrid Captcha System (FIXED V2)
Includes robust selector list for Month/Form pages
"""

import time
import logging
import base64
import requests
import abc
from typing import Optional, List, Tuple
from playwright.sync_api import Page
import numpy as np

# Import config
from .config import Config

# Setup Logger
logger = logging.getLogger("EliteSniperV2.Captcha")

# Optional Imports
try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False

try:
    from . import notifier
    NOTIFIER_AVAILABLE = True
except ImportError:
    NOTIFIER_AVAILABLE = False

# ==============================================================================
# 1. STRATEGY PATTERN
# ==============================================================================

class CaptchaStrategy(abc.ABC):
    @abc.abstractmethod
    def solve(self, image_bytes: bytes) -> str:
        pass
    def name(self) -> str:
        return self.__class__.__name__

class CapSolverStrategy(CaptchaStrategy):
    """CapSolver (Synchronous & Fast)"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://api.capsolver.com/createTask"
        
    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key: return ""
        try:
            img_b64 = base64.b64encode(image_bytes).decode('utf-8').replace("\n", "")
            payload = {
                "clientKey": self.api_key,
                "task": {
                    "type": "ImageToTextTask",
                    "module": "common",
                    "body": img_b64
                }
            }
            resp = requests.post(self.url, json=payload, timeout=10)
            data = resp.json()
            if data.get('status') == 'ready':
                return data.get('solution', {}).get('text')
            return ""
        except Exception as e:
            logger.error(f"[CapSolver] Error: {e}")
            return ""

class CapMonsterStrategy(CaptchaStrategy):
    """CapMonster Cloud"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.create_url = "https://api.capmonster.cloud/createTask"
        self.res_url = "https://api.capmonster.cloud/getTaskResult"
        
    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key: return ""
        try:
            img_b64 = base64.b64encode(image_bytes).decode('utf-8').replace("\n", "")
            payload = {"clientKey": self.api_key, "task": {"type": "ImageToTextTask", "body": img_b64}}
            resp = requests.post(self.create_url, json=payload, timeout=10)
            if resp.json().get("errorId") != 0: return ""
            task_id = resp.json().get("taskId")
            for _ in range(20):
                time.sleep(0.5)
                res = requests.post(self.res_url, json={"clientKey": self.api_key, "taskId": task_id}, timeout=5).json()
                if res.get("status") == "ready": return res.get("solution", {}).get("text")
            return ""
        except: return ""

class LocalDDDDOCRStrategy(CaptchaStrategy):
    """Local Fallback"""
    def __init__(self):
        if DDDDOCR_AVAILABLE:
            self.ocr = ddddocr.DdddOcr(beta=True)
        else:
            self.ocr = None
    def solve(self, image_bytes: bytes) -> str:
        if not self.ocr: return ""
        try:
            return self.ocr.classification(image_bytes)
        except: return ""

# ==============================================================================
# 2. MANUAL HANDLER
# ==============================================================================

class TelegramCaptchaHandler:
    def __init__(self, c2_instance=None):
        self.enabled = Config.MANUAL_CAPTCHA_ENABLED and NOTIFIER_AVAILABLE
        self.timeout = Config.MANUAL_CAPTCHA_TIMEOUT
        self.c2 = c2_instance
    
    def request_manual_solution(self, image_bytes: bytes, location: str, session_age: int = 0, **kwargs) -> Optional[str]:
        if not self.enabled: return None
        caption = f"🔐 CAPTCHA ({location})\nAge: {session_age}s"
        try:
            notifier.send_photo_bytes(image_bytes, caption)
        except: pass
        
        if self.c2:
            return self.c2.wait_for_captcha(timeout=self.timeout)
        else:
            return notifier.wait_for_captcha_reply(timeout=self.timeout)

# ==============================================================================
# 3. ENHANCED CONTROLLER
# ==============================================================================

class EnhancedCaptchaSolver:
    def __init__(self, mode: str = "HYBRID", c2_instance=None):
        self.mode = Config.EXECUTION_MODE
        self.c2 = c2_instance
        self.manual_handler = TelegramCaptchaHandler(c2_instance)
        self.strategy = self._select_strategy()
        self._pre_solved_code = None
        self._pre_solved_time = 0
        logger.info(f"[Captcha] Initialized. Strategy: {self.strategy.name()} | Mode: {self.mode}")

    def _select_strategy(self) -> CaptchaStrategy:
        prov = Config.CAPTCHA_PROVIDER
        key = Config.CAPTCHA_API_KEY
        if prov == "CAPSOLVER" and key: return CapSolverStrategy(key)
        if prov == "CAPMONSTER" and key: return CapMonsterStrategy(key)
        return LocalDDDDOCRStrategy()

    def _clean_result(self, text: str) -> str:
        if not text: return ""
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join([c for c in text if c in allowed]).lower()

    def validate_captcha_result(self, code: str) -> Tuple[bool, str]:
        if not code: return False, "EMPTY"
        code = self._clean_result(code)
        length = len(code)
        black_patterns = ["4333", "333", "444", "0000", "4444", "1111"]
        if code in black_patterns or (len(set(code)) == 1 and length > 3):
            return False, "BLACK_DETECTED"
        if length == 6: return True, "VALID"
        if length in [7, 8]: return True, f"AGING_{length}"
        if length < 4: return False, "TOO_SHORT"
        return False, "INVALID_LEN"

    def solve(self, image_bytes: bytes, location: str = "SOLVE") -> Tuple[str, str]:
        if len(image_bytes) < 2000: return "", "BLACK_IMAGE"
        code = self.strategy.solve(image_bytes)
        code = self._clean_result(code)
        is_valid, status = self.validate_captcha_result(code)
        if is_valid:
            logger.info(f"[{location}] Solved via {self.strategy.name()}: '{code}'")
            return code, status
        return "", status

    def _get_captcha_image(self, page: Page, location: str) -> Optional[bytes]:
        try:
            div = page.locator("captcha > div").first
            if div.is_visible(timeout=500):
                style = div.get_attribute("style") or ""
                import re
                match = re.search(r"base64,([A-Za-z0-9+/=]+)", style)
                if match: return base64.b64decode(match.group(1))
        except: pass
        selectors = ["captcha > div", "div.captcha-image", "div#captcha", "img[alt*='captcha']"]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=500): return el.screenshot()
            except: continue
        return None

    def solve_from_page(self, page: Page, location: str = "GENERAL", timeout: int = 10000, session_age: int = 0, attempt: int = 1, max_attempts: int = 5) -> Tuple[bool, Optional[str], str]:
        if self._pre_solved_code and (time.time() - self._pre_solved_time < 30):
            code = self._pre_solved_code
            self._pre_solved_code = None
            return True, code, "PRE_SOLVED"

        img_bytes = self._get_captcha_image(page, location)
        if not img_bytes: return False, None, "NO_IMAGE"
        
        code, status = self.solve(img_bytes, location)
        if code and "VALID" in status or "AGING" in status:
            return True, code, status
            
        if self.mode != "AUTO" and Config.MANUAL_CAPTCHA_ENABLED:
            man_code = self.manual_handler.request_manual_solution(img_bytes, location, session_age)
            if man_code: return True, man_code, "MANUAL"
                
        return False, None, status

    def solve_form_captcha_with_retry(self, page: Page, location: str = "FORM_RETRY", max_attempts: int = 5, session_age: int = 0) -> Tuple[bool, Optional[str], str]:
        for i in range(max_attempts):
            success, code, status = self.solve_from_page(page, f"{location}_{i+1}", session_age=session_age)
            if success: return True, code, status
            if i < max_attempts - 1:
                self.reload_captcha(page)
                time.sleep(1)
        return False, None, "MAX_RETRIES"

    def pre_solve(self, page: Page, location: str) -> Tuple[bool, Optional[str], str]:
        img_bytes = self._get_captcha_image(page, location)
        if not img_bytes: return False, None, "NO_IMAGE"
        code, status = self.solve(img_bytes, location)
        if code:
            self._pre_solved_code = code
            self._pre_solved_time = time.time()
            return True, code, status
        return False, None, status

    def safe_captcha_check(self, page: Page, location: str) -> Tuple[bool, bool]:
        try:
            content = page.content().lower()
            if not any(k in content for k in ["captcha", "security code", "verkaptxt"]):
                return False, True
            selectors = ["input[name='captchaText']", "input[name='captcha']", "input#captchaText"]
            for sel in selectors:
                if page.locator(sel).first.is_visible(timeout=2000):
                    return True, True
            return False, True
        except: return False, False

    def reload_captcha(self, page: Page, location: str="RELOAD") -> bool:
        """Robust reload with Month & Form page support"""
        selectors = [
            # Form Page
            "#appointment_newAppointmentForm_form_newappointment_refreshcaptcha",
            "input[name='action:appointment_refreshCaptcha']",
            # Month Page (Category) - CRITICAL FOR LOOP FIX
            "#appointment_captcha_month_refreshcaptcha",
            "input[name='action:appointment_refreshCaptchamonth']",
            # General Fallback
            "input[value='Load another picture']",
            "input[value='Bild laden']"
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible():
                    btn.click()
                    return True
            except: continue
        # JS Fallback
        try:
            return page.evaluate("""
                const btns = Array.from(document.querySelectorAll('input[type=submit], button'));
                const target = btns.find(b => (b.value||b.innerText||'').toLowerCase().includes('load another'));
                if(target) { target.click(); return true; }
                return false;
            """)
        except: return False

    def submit_captcha(self, page: Page, method: str="auto") -> bool:
        try:
            buttons = ["input[name='submit']", "input[value='Weiter']", "button:has-text('Weiter')"]
            for b in buttons:
                if page.locator(b).first.is_visible():
                    page.locator(b).first.click()
                    return True
            page.keyboard.press("Enter")
            return True
        except: return False