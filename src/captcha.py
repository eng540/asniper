"""
Elite Sniper v2.0 - High-Performance Captcha System
Architecture: Strategy Pattern with Synchronous Optimization
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
from . import notifier

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

# ==============================================================================
# STRATEGY INTERFACE
# ==============================================================================

class CaptchaStrategy(abc.ABC):
    """Abstract Base Class for Captcha Solvers"""
    
    @abc.abstractmethod
    def solve(self, image_bytes: bytes) -> str:
        """Solve the captcha image and return the code"""
        pass

    def name(self) -> str:
        return self.__class__.__name__

# ==============================================================================
# STRATEGY: LOCAL (ddddocr)
# ==============================================================================

class LocalDDDDOCRStrategy(CaptchaStrategy):
    """Local solver using ddddocr (Backup only in Production)"""
    
    def __init__(self):
        if not DDDDOCR_AVAILABLE:
            raise ImportError("ddddocr is not installed")
        self.ocr = ddddocr.DdddOcr(beta=True)
        
    def _preprocess(self, image_bytes: bytes) -> bytes:
        """Apply V1 Strong Preprocessing (Only needed for local OCR)"""
        if not OPENCV_AVAILABLE: return image_bytes
        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            gray = clahe.apply(gray)
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _, encoded_img = cv2.imencode('.png', thresh)
            return encoded_img.tobytes()
        except:
            return image_bytes

    def solve(self, image_bytes: bytes) -> str:
        try:
            # Try raw first for speed
            res = self.ocr.classification(image_bytes)
            if len(res) == 6: return res
            # Retry with heavy preprocessing
            return self.ocr.classification(self._preprocess(image_bytes))
        except Exception:
            return ""

# ==============================================================================
# STRATEGY: CAPSOLVER (OPTIMIZED)
# ==============================================================================

class CapSolverStrategy(CaptchaStrategy):
    """
    CapSolver Strategy - Optimized for Production
    Uses 'ImageToTextTask' in Synchronous mode (CreateTask returns result directly)
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        # For ImageToText, createTask returns the solution immediately if configured right
        self.url = "https://api.capsolver.com/createTask"
        
    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key: return ""
        
        try:
            # Clean Base64 (No newlines allowed)
            img_b64 = base64.b64encode(image_bytes).decode('utf-8').replace("\n", "")
            
            payload = {
                "clientKey": self.api_key,
                "task": {
                    "type": "ImageToTextTask",
                    "module": "common", # 'common' handles mixed alphanumeric well
                    "body": img_b64
                }
            }
            
            # Send Request (Timeout 10s is plenty for sync API)
            logger.info("[CapSolver] Sending sync request...")
            resp = requests.post(self.url, json=payload, timeout=10)
            data = resp.json()
            
            # Check for immediate error
            if data.get('errorId') != 0:
                logger.error(f"[CapSolver] Error: {data.get('errorDescription')}")
                return ""
            
            # Direct Result Extraction
            if data.get('status') == 'ready':
                solution = data.get('solution', {}).get('text')
                logger.info(f"[CapSolver] SOLVED: {solution}")
                return solution
                
            logger.warning(f"[CapSolver] Unexpected status: {data.get('status')}")
            return ""
            
        except Exception as e:
            logger.error(f"[CapSolver] Exception: {e}")
            return ""

# ==============================================================================
# STRATEGY: OTHERS (CapMonster, 2Captcha)
# ==============================================================================

class CapMonsterStrategy(CaptchaStrategy):
    """CapMonster Cloud Strategy"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.create_url = "https://api.capmonster.cloud/createTask"
        self.res_url = "https://api.capmonster.cloud/getTaskResult"
        
    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key: return ""
        try:
            img_b64 = base64.b64encode(image_bytes).decode('utf-8').replace("\n", "")
            payload = {"clientKey": self.api_key, "task": {"type": "ImageToTextTask", "body": img_b64}}
            
            # Create
            resp = requests.post(self.create_url, json=payload, timeout=10)
            if resp.json().get("errorId") != 0: return ""
            task_id = resp.json().get("taskId")
            
            # Poll
            for _ in range(20): # 10 seconds max
                time.sleep(0.5)
                res = requests.post(self.res_url, json={"clientKey": self.api_key, "taskId": task_id}, timeout=5).json()
                if res.get("status") == "ready": 
                    return res.get("solution", {}).get("text")
            return ""
        except: return ""

class TwoCaptchaStrategy(CaptchaStrategy):
    """2Captcha Strategy"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.in_url = "http://2captcha.com/in.php"
        self.res_url = "http://2captcha.com/res.php"
        
    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key: return ""
        try:
            img_b64 = base64.b64encode(image_bytes).decode('utf-8')
            resp = requests.post(self.in_url, data={'key': self.api_key, 'method': 'base64', 'body': img_b64, 'json': 1})
            if resp.json().get('status') != 1: return ""
            req_id = resp.json().get('request')
            
            for _ in range(20):
                time.sleep(2)
                res = requests.get(f"{self.res_url}?key={self.api_key}&action=get&id={req_id}&json=1").json()
                if res.get('status') == 1: return res.get('request')
            return ""
        except: return ""

# ==============================================================================
# MAIN CONTROLLER (EnhancedCaptchaSolver)
# ==============================================================================

class EnhancedCaptchaSolver:
    """
    Main Controller. Handles Strategy selection, Validation, and Page Interaction.
    """
    
    def __init__(self, mode: str = "AUTO", c2_instance=None):
        # Override mode with Config if set to AUTO/Production
        self.mode = Config.EXECUTION_MODE
        self.c2 = c2_instance
        self.strategy = self._select_strategy()
        logger.info(f"[Captcha] System Active. Provider: {self.strategy.name()} | Mode: {self.mode}")

    def _select_strategy(self):
        prov = Config.CAPTCHA_PROVIDER
        key = Config.CAPTCHA_API_KEY
        
        if prov == "CAPSOLVER" and key: return CapSolverStrategy(key)
        if prov == "CAPMONSTER" and key: return CapMonsterStrategy(key)
        if prov == "2CAPTCHA" and key: return TwoCaptchaStrategy(key)
        
        logger.warning("No valid External Provider configured. Fallback to LOCAL.")
        return LocalDDDDOCRStrategy()

    def _validate(self, code: str) -> Tuple[bool, str]:
        if not code: return False, "EMPTY"
        # Cleaning
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        code = "".join([c for c in code if c in allowed]).lower()
        
        if len(code) == 6: return True, "VALID"
        if len(code) in [7, 8]: return True, "AGING_OK"
        return False, "INVALID_LEN"

    def solve(self, image_bytes: bytes, location: str = "SOLVE") -> Tuple[str, str]:
        # 1. Black Captcha Check (Poisoned session)
        if len(image_bytes) < 2000:
            logger.critical(f"[{location}] BLACK CAPTCHA DETECTED - ABORT")
            return "", "BLACK_IMAGE"

        # 2. Strategy Execution
        start = time.time()
        code = self.strategy.solve(image_bytes)
        duration = time.time() - start
        
        # 3. Validation
        is_valid, status = self._validate(code)
        
        if is_valid:
            logger.info(f"[{location}] Solved: '{code}' in {duration:.2f}s ({status})")
            return code, status
            
        # 4. Manual Fallback (Only in HYBRID/MANUAL modes)
        if self.mode != "AUTO" and Config.MANUAL_CAPTCHA_ENABLED:
            logger.warning(f"[{location}] Auto failed ({status}). Trying Telegram...")
            # Simple telegram send wrapper
            try:
                notifier.send_photo_bytes(image_bytes, f"CAPTCHA ({location})\nReply 6 chars:")
                reply = notifier.wait_for_captcha_reply(timeout=Config.MANUAL_CAPTCHA_TIMEOUT)
                if reply: return reply, "MANUAL"
            except: pass
            
        return "", status

    # --- Page Interaction Methods ---

    def safe_captcha_check(self, page: Page, location: str = "CHECK") -> Tuple[bool, bool]:
        """Verify if captcha is present"""
        try:
            # Check content text first (fastest)
            txt = page.content().lower()
            if not any(k in txt for k in ["captcha", "security code", "verkaptxt"]):
                return False, True
            
            # Check selectors
            selectors = ["input[name='captchaText']", "input[name='captcha']", "input#captchaText"]
            for s in selectors:
                if page.locator(s).first.is_visible(timeout=1000):
                    return True, True
            return False, True
        except: return False, False

    def solve_from_page(self, page: Page, location: str = "PAGE") -> Tuple[bool, Optional[str]]:
        """Full workflow: Find -> Solve -> Fill"""
        try:
            # 1. Get Image
            img_bytes = None
            # Try Base64 first (Most common on this site)
            try:
                div = page.locator("captcha > div").first
                if div.is_visible(timeout=500):
                    style = div.get_attribute("style") or ""
                    import re
                    m = re.search(r"base64,([A-Za-z0-9+/=]+)", style)
                    if m: img_bytes = base64.b64decode(m.group(1))
            except: pass
            
            if not img_bytes:
                # Screenshot fallback
                try:
                    el = page.locator("captcha > div").first
                    if el.is_visible(): img_bytes = el.screenshot()
                except: return False, None

            if not img_bytes: return False, None # No image found

            # 2. Solve
            code, status = self.solve(img_bytes, location)
            if not code: return False, None
            
            # 3. Fill
            page.fill("input[name='captchaText']", code)
            return True, code
            
        except Exception as e:
            logger.error(f"[{location}] Flow Error: {e}")
            return False, None

    def submit_captcha(self, page: Page) -> bool:
        """Submit the form"""
        try:
            # Specific button or Enter key
            btn = page.locator("input[name='submit']").first
            if btn.is_visible():
                btn.click()
            else:
                page.keyboard.press("Enter")
            return True
        except: return False
        
    def verify_captcha_solved(self, page: Page) -> Tuple[bool, str]:
        """Check result"""
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
            url = page.url.lower()
            if "appointment_showday" in url: return True, "DAY_PAGE"
            if "appointment_showform" in url: return True, "FORM_PAGE"
            
            content = page.content().lower()
            if "security code" in content and "not correct" in content:
                return False, "WRONG"
            return True, "UNKNOWN" # Assume success if no error
        except: return False, "ERROR"

# Backward Compatibility Wrapper
class CaptchaSolver:
    def __init__(self):
        self.solver = EnhancedCaptchaSolver()
    def solve(self, image_bytes: bytes) -> str:
        code, _ = self.solver.solve(image_bytes, "LEGACY")
        return code