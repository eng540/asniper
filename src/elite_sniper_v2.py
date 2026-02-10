"""
Elite Sniper v2.0 - Production-Grade Multi-Session Appointment Booking System

Integrates best features from:
- Elite Sniper: Multi-session architecture, Scout/Attacker pattern, Scheduled activation
- KingSniperV12: State Machine, Soft Recovery, Safe Captcha Check, Debug utilities

Architecture:
- 3 Parallel Sessions (Architecture ready)
- 24/7 Operation with 2:00 AM Aden time activation
- Intelligent session lifecycle management
- Production-grade error handling and recovery

Version: 2.1.0 (Stale Loop Fix)
"""

import time
import random
import datetime
import logging
import os
import sys
import re
from typing import List, Tuple, Optional, Dict, Any
from threading import Thread, Event, Lock
from dataclasses import asdict

import pytz
from playwright.sync_api import sync_playwright, Page, BrowserContext, Browser

# Internal imports
try:
    from .config import Config
except ImportError:
    from config import Config
from .ntp_sync import NTPTimeSync
from .session_state import (
    SessionState, SessionStats, SystemState, SessionHealth, 
    SessionRole, Incident, IncidentManager, IncidentType, IncidentSeverity
)
from .captcha import EnhancedCaptchaSolver
from .notifier import send_alert, send_photo, send_success_notification, send_status_update
from .debug_utils import DebugManager
from .page_flow import PageFlowDetector

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'elite_sniper_v2.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger("EliteSniperV2")


class EliteSniperV2:
    """
    Production-Grade Multi-Session Appointment Booking System
    """
    
    VERSION = "2.1.0"
    
    def __init__(self, run_mode: str = "AUTO"):
        """Initialize Elite Sniper v2.0"""
        self.run_mode = run_mode
        
        logger.info("=" * 70)
        logger.info(f"[INIT] ELITE SNIPER V{self.VERSION} - INITIALIZING")
        logger.info(f"[MODE] Running Mode: {self.run_mode}")
        logger.info("=" * 70)
        
        # Validate configuration
        self._validate_config()
        
        # Session management
        self.session_id = f"elite_v2_{int(time.time())}_{random.randint(1000, 9999)}"
        self.start_time = datetime.datetime.now()
        
        # System state
        self.system_state = SystemState.STANDBY
        self.stop_event = Event()      # Global kill switch
        self.slot_event = Event()      # Scout → Attacker signal
        self.target_url: Optional[str] = None  # Discovered appointment URL
        self.lock = Lock()              # Thread-safe coordination
        self.screenshot_requested = Event()  # Flag for screenshot request
        
        # Initialize Telegram C2 FIRST so we can pass it
        try:
            from .telegram_c2 import TelegramCommander
            self.c2 = TelegramCommander(bot_instance=self)
            self.c2.start()
        except Exception as e:
            logger.error(f"[C2] Failed to start Telegram Commander: {e}")
            self.c2 = None
            
        # Components
        # EXECUTION MODES: AUTO, MANUAL, HYBRID
        self.mode = Config.EXECUTION_MODE
        logger.info(f"[MODE] Execution Strategy: {self.mode}")
        
        # Pass C2 to solver
        self.solver = EnhancedCaptchaSolver(mode=self.mode, c2_instance=self.c2)
        
        self.debug_manager = DebugManager(self.session_id, Config.EVIDENCE_DIR)
        self.incident_manager = IncidentManager()
        self.ntp_sync = NTPTimeSync(Config.NTP_SERVERS, Config.NTP_SYNC_INTERVAL)
        self.page_flow = PageFlowDetector()  # For accurate page type detection
        self.paused = Event() # Pause control
        
        # Configuration
        self.base_url = self._prepare_base_url(Config.TARGET_URL)
        self.timezone = pytz.timezone(Config.TIMEZONE)
        
        # User agents for rotation
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]
        
        # Proxies (optional)
        self.proxies = self._load_proxies()
        
        # Global statistics
        self.global_stats = SessionStats()
        
        # Start background NTP sync
        self.ntp_sync.start_background_sync()
        
        logger.info(f"[ID] Session ID: {self.session_id}")
        logger.info(f"[URL] Base URL: {self.base_url[:60]}...")
        logger.info(f"[TZ] Timezone: {self.timezone}")
        logger.info(f"[NTP] NTP Offset: {self.ntp_sync.offset:.4f}s")
        logger.info(f"[DIR] Evidence Dir: {self.debug_manager.session_dir}")
        logger.info(f"[PROXY] Proxies: {len([p for p in self.proxies if p])} configured")
        logger.info(f"[OK] Initialization complete")
    
    # ==================== Configuration ====================
    
    def request_screenshot(self):
        """Set flag to request screenshot on next loop cycle"""
        self.screenshot_requested.set()

    def set_mode(self, new_mode: str): 
        """Update execution mode correctly""" 
        valid_modes = ["AUTO", "MANUAL", "HYBRID"] 
        if new_mode.upper() in valid_modes: 
            self.mode = new_mode.upper() 
            self.run_mode = new_mode.upper() 
            if hasattr(self, 'solver'): 
                self.solver.mode = self.mode 
            logger.info(f"[MODE] Switched to {self.mode}") 
            return True 
        return False

    def pause_execution(self): 
        """Pause the scanning loop""" 
        if not self.paused.is_set(): 
            self.paused.set() 
            logger.info("⏸️ System PAUSED by user")

    def resume_execution(self): 
        """Resume the scanning loop""" 
        if self.paused.is_set(): 
            self.paused.clear() 
            logger.info("▶️ System RESUMED by user")

    def get_status_report(self) -> str:
        """Generate status report for C2"""
        stats = self.global_stats
        status = "🟢 Running" if not self.paused.is_set() else "⏸ Paused"
        
        return (
            f"📊 <b>System Status</b>\n"
            f"Mode: {self.run_mode}\n"
            f"State: {status}\n\n"
            f"📉 <b>Statistics</b>:\n"
            f"Days Found: {stats.days_found}\n"
            f"Slots Found: {stats.slots_found}\n"
            f"Forms Filled: {stats.forms_filled}\n"
            f"Captchas: {stats.captchas_solved}/{stats.captchas_solved + stats.captchas_failed}\n"
        )
    
    def force_screenshot(self) -> Optional[str]:
        """Take immediate screenshot for C2"""
        try:
            if hasattr(self, 'current_page') and self.current_page:
                try:
                    timestamp = int(time.time())
                    filename = f"c2_shot_{timestamp}.jpg"
                    path = os.path.join(Config.EVIDENCE_DIR, filename)
                    self.current_page.screenshot(path=path)
                    return path
                except Exception as e:
                    logger.error(f"[C2] Screenshot failed: {e}")
            return None
        except Exception as e:
            logger.error(f"[C2] Force screenshot error: {e}")
            return None

    def _validate_config(self):
        """Validate required configuration"""
        required = [
            'TARGET_URL', 'LAST_NAME', 'FIRST_NAME', 
            'EMAIL', 'PASSPORT', 'PHONE'
        ]
        
        missing = [field for field in required if not getattr(Config, field, None)]
        
        if missing:
            raise ValueError(f"[ERR] Missing configuration: {', '.join(missing)}")
        
        logger.info("[OK] Configuration validated")
    
    def cleanup(self):
        """Robust resource cleanup"""
        logger.info("[CLEANUP] Initiating robust shutdown...")
        self.stop_event.set()
        try:
            if hasattr(self, 'ntp_sync'):
                self.ntp_sync.stop_background_sync()
        except: pass
        try:
            if hasattr(self, 'c2') and self.c2:
                self.c2.stop()
        except: pass
        logger.info("[CLEANUP] Resources released")
    
    def _prepare_base_url(self, url: str) -> str:
        """Prepare base URL with locale"""
        if "request_locale" not in url:
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}request_locale=en"
        return url
    
    def _load_proxies(self) -> List[Optional[str]]:
        """Load proxies from config or file"""
        proxies = []
        if hasattr(Config, 'PROXIES') and Config.PROXIES:
            proxies.extend([p for p in Config.PROXIES if p])
        
        try:
            if os.path.exists("proxies.txt"):
                with open("proxies.txt") as f:
                    file_proxies = [line.strip() for line in f if line.strip()]
                    proxies.extend(file_proxies)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load proxies.txt: {e}")
        
        while len(proxies) < 3:
            proxies.append(None)
        
        return proxies[:3]
    
    # ==================== Time Management ====================
    
    def get_current_time_aden(self) -> datetime.datetime:
        corrected_utc = self.ntp_sync.get_corrected_time()
        aden_time = corrected_utc.replace(tzinfo=pytz.UTC).astimezone(self.timezone)
        return aden_time
    
    def is_pre_attack(self) -> bool:
        now = self.get_current_time_aden()
        return (now.hour == 1 and 
                now.minute == Config.PRE_ATTACK_MINUTE and 
                now.second >= Config.PRE_ATTACK_SECOND)
    
    def is_attack_time(self) -> bool:
        now = self.get_current_time_aden()
        return now.hour == Config.ATTACK_HOUR and now.minute < Config.ATTACK_WINDOW_MINUTES
    
    def get_sleep_interval(self) -> float:
        if self.is_attack_time():
            return random.uniform(Config.ATTACK_SLEEP_MIN, Config.ATTACK_SLEEP_MAX)
        elif self.is_pre_attack():
            return Config.PRE_ATTACK_SLEEP
        else:
            now = self.get_current_time_aden()
            if now.hour == 1 and now.minute >= 45:
                return Config.WARMUP_SLEEP
            return random.uniform(Config.PATROL_SLEEP_MIN, Config.PATROL_SLEEP_MAX)
    
    def get_mode(self) -> str:
        if self.is_attack_time():
            return "ATTACK"
        elif self.is_pre_attack():
            return "PRE_ATTACK"
        else:
            now = self.get_current_time_aden()
            if now.hour == 1 and now.minute >= 45:
                return "WARMUP"
            return "PATROL"
    
    # ==================== Session Management ====================
    
    def create_context(
        self, 
        browser: Browser, 
        worker_id: int,
        proxy: Optional[str] = None
    ) -> Tuple[BrowserContext, Page, SessionState]:
        try:
            role = SessionRole.SCOUT if worker_id == 1 else SessionRole.ATTACKER
            user_agent = random.choice(self.user_agents)
            
            viewport_width = 1366 + random.randint(0, 50)
            viewport_height = 768 + random.randint(0, 30)
            
            context_args = {
                "user_agent": user_agent,
                "viewport": {"width": viewport_width, "height": viewport_height},
                "locale": "en-US",
                "timezone_id": "Asia/Aden",
                "ignore_https_errors": True
            }
            
            if proxy:
                context_args["proxy"] = {"server": proxy}
            
            context = browser.new_context(**context_args)
            page = context.new_page()
            
            # Anti-detection + Keep-Alive script
            page.add_init_script(f"""
                Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
                Object.defineProperty(navigator, 'plugins', {{ get: () => [1, 2, 3, 4, 5] }});
                Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
                setInterval(() => {{
                    fetch(location.href, {{ method: 'HEAD' }}).catch(()=>{{}});
                }}, {Config.HEARTBEAT_INTERVAL * 1000});
            """)
            
            context.set_default_timeout(25000)
            context.set_default_navigation_timeout(30000)
            
            def route_handler(route):
                resource_type = route.request.resource_type
                if resource_type in ["image", "media", "font", "stylesheet"]:
                    route.abort()
                else:
                    route.continue_()
            
            page.route("**/*", route_handler)
            
            session_state = SessionState(
                session_id=f"{self.session_id}_w{worker_id}",
                role=role,
                worker_id=worker_id,
                max_age=300,
                max_idle=Config.SESSION_MAX_IDLE,
                max_failures=Config.MAX_CONSECUTIVE_ERRORS,
                max_captcha_attempts=Config.MAX_CAPTCHA_ATTEMPTS
            )
            
            logger.info(f"[CTX] [W{worker_id}] Context created - Role: {role.value}")
            with self.lock:
                self.global_stats.rebirths += 1
            
            return context, page, session_state
            
        except Exception as e:
            logger.error(f"[ERR] [W{worker_id}] Context creation failed: {e}")
            raise
    
    def generate_month_urls(self) -> List[str]:
        """Generate priority month URLs"""
        try:
            today = datetime.datetime.now().date()
            base_clean = self.base_url.split("&dateStr=")[0] if "&dateStr=" in self.base_url else self.base_url
            urls = []
            priority_offsets = [2, 3, 4, 5] 
            for offset in priority_offsets:
                future_date = today + datetime.timedelta(days=30 * offset)
                date_str = f"15.{future_date.month:02d}.{future_date.year}" 
                url = f"{base_clean}&dateStr={date_str}"
                urls.append(url)
            logger.info(f"📋 Generated {len(urls)} month URLs")
            return urls
        except Exception as e:
            logger.error(f"❌ Month URL generation failed: {e}")
            return []
    
    def select_category_by_value(self, page: Page) -> bool:
        """Smart Targeting: Select category using keyword priority search."""
        try:
            selects = page.locator("select").all()
            if not selects: return False
            
            all_options = []
            for select in selects:
                try:
                    options = select.locator("option").all()
                    for option in options:
                        text = option.inner_text().strip()
                        value = option.get_attribute("value")
                        if text and value:
                            all_options.append({
                                "select": select,
                                "text_lower": text.lower(),
                                "value": value
                            })
                except: continue
            
            for keyword in Config.TARGET_KEYWORDS:
                keyword_lower = keyword.lower()
                for opt in all_options:
                    if keyword_lower in opt["text_lower"]:
                        try:
                            opt["select"].select_option(value=opt["value"])
                            page.evaluate("""
                                const selects = document.querySelectorAll('select');
                                selects.forEach(s => {
                                    s.dispatchEvent(new Event('input', { bubbles: true }));
                                    s.dispatchEvent(new Event('change', { bubbles: true }));
                                });
                            """)
                            return True
                        except: continue
            
            valid_options = [opt for opt in all_options if opt["value"]]
            if len(valid_options) >= 2:
                fallback_opt = valid_options[1]
                try:
                    fallback_opt["select"].select_option(value=fallback_opt["value"])
                    page.evaluate("""
                        const selects = document.querySelectorAll('select');
                        selects.forEach(s => {
                            s.dispatchEvent(new Event('input', { bubbles: true }));
                            s.dispatchEvent(new Event('change', { bubbles: true }));
                        });
                    """)
                    return True
                except: pass
            return False
        except: return False
    
    # ==================== Single Session Mode ====================
    
    def _run_single_session(self, browser: Browser, worker_id: int = 1):
        """Run a single robust blocking session"""
        worker_logger = logging.getLogger(f"Worker-{worker_id}")
        
        try:
            proxy = Config.PROXIES[worker_id % len(Config.PROXIES)] if Config.PROXIES else None
        except: proxy = None
        
        context, page, session = self.create_context(self.browser, worker_id, proxy)
        self.current_page = page 
        session.role = SessionRole.SCOUT
        
        worker_logger.info(f"[START] Robust Single Session Mode")
        
        try:
            max_cycles = 1000
            
            for cycle in range(max_cycles):
                if self.stop_event.is_set(): break
                
                if self.paused.is_set():
                    worker_logger.info("⏸️ Session PAUSED by C2")
                    while self.paused.is_set() and not self.stop_event.is_set():
                        time.sleep(1)
                    worker_logger.info("▶️ Session RESUMED")
                
                worker_logger.info(f"🔄 [CYCLE {cycle+1}] Scanning...")
                
                try:
                    month_urls = self.generate_month_urls()
                    
                    for url in month_urls:
                        if self.stop_event.is_set(): break
                        
                        if self._process_month_page(page, session, url, worker_logger):
                            return 
                        
                        if getattr(session, 'consecutive_network_failures', 0) >= 2:
                             worker_logger.warning("⚡ Circuit Breaker Triggered: Network Unstable.")
                             break
                        
                        time.sleep(random.uniform(1, 2))
                    
                    sleep_time = self.get_sleep_interval()
                    worker_logger.info(f"💤 Sleeping {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                    
                    if session.age() > Config.SESSION_MAX_AGE or getattr(session, 'consecutive_network_failures', 0) >= 2:
                        reason = "Age" if session.age() > Config.SESSION_MAX_AGE else "Network Instability"
                        worker_logger.info(f"♻️ Session Reset triggered ({reason})")
                        try: 
                            page.close()
                            context.close()
                        except: pass
                        context, page, session = self.create_context(browser, worker_id, proxy)
                        session.role = SessionRole.SCOUT
                        
                except Exception as cycle_error:
                    worker_logger.error(f"⚠️ Cycle error: {cycle_error}")
                    try:
                        page.close()
                        context.close()
                    except: pass
                    context, page, session = self.create_context(browser, worker_id, proxy)
                    session.role = SessionRole.SCOUT

        except Exception as e:
            worker_logger.error(f"❌ Critical Session Error: {e}", exc_info=True)
        finally:
            worker_logger.info("🧹 Final cleanup of single session...")
            try: 
                page.close()
                context.close()
            except: pass

    def _analyze_page_state(self, page: Page, logger) -> str:
        """Analyzes the current page HTML."""
        try:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except: pass
            
            time.sleep(0.3)
            content = page.content().lower()
            
            try:
                slot_count = page.locator("a[href*='appointment_showDay']").count()
                if slot_count > 0:
                    logger.info(f"🎯 Detected {slot_count} available day(s)!")
                    return "SLOTS_FOUND"
            except: pass
            
            if "unfortunately, there are no appointments available" in content:
                return "EMPTY_CALENDAR"
            if "keine termine" in content:
                return "EMPTY_CALENDAR"
            if "no appointments" in content and "appointment_showDay" not in content:
                return "EMPTY_CALENDAR"
            
            if "entered text was wrong" in content:
                return "WRONG_CODE"
            try:
                if page.locator("div.global-error").is_visible(timeout=300):
                    return "WRONG_CODE"
            except: pass
            
            try:
                if page.locator("#appointment_captcha_month").is_visible(timeout=300):
                    return "CAPTCHA"
            except: pass
            
            try:
                if page.locator("input[name='captchaText']").is_visible(timeout=300):
                    return "CAPTCHA"
            except: pass

            return "UNKNOWN"
            
        except Exception as e:
            logger.warning(f"⚠️ Page state analysis error: {e}")
            return "UNKNOWN"

    def _process_month_page(self, page: Page, session: SessionState, url: str, logger) -> bool:
        """
        Smart Month Page Handler with STALE LOOP PROTECTION
        """
        try:
            # A. Navigation
            logger.info(f"🌐 Navigating to: {url[:60]}...")
            for nav_attempt in range(2):
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    break
                except Exception as nav_e:
                    if nav_attempt == 1: 
                        logger.error(f"❌ Max navigation retries reached")
                        session.consecutive_network_failures += 1
                        return False
                    time.sleep(5)
            
            session.current_url = url
            session.touch()
            session.consecutive_network_failures = 0
            self.global_stats.pages_loaded += 1
            
            # B. SMART CAPTCHA SOLVING LOOP
            max_attempts = 5
            
            for attempt in range(max_attempts):
                # 1. FORCED REFRESH (The Fix for Stale Loop)
                if attempt > 0:
                    logger.info(f"🔄 Attempt {attempt+1}: Refreshing captcha to avoid stale loop...")
                    if self.solver.reload_captcha(page, "MONTH_LOOP"):
                        time.sleep(2)
                    else:
                        logger.warning("⚠️ Refresh button not found, reloading page...")
                        page.reload(wait_until="domcontentloaded")
                        time.sleep(1)

                # 2. Analyze State
                state = self._analyze_page_state(page, logger)
                logger.info(f"🧐 Page State Analysis [{attempt+1}/{max_attempts}]: {state}")
                
                if state == "SLOTS_FOUND":
                    logger.critical("🎯 SUCCESS: Days detected!")
                    day_links = page.locator("a[href*='appointment_showDay']").all()
                    if day_links:
                        self.global_stats.days_found += len(day_links)
                        target = day_links[0]
                        href = target.get_attribute("href")
                        if href:
                            base = self.base_url.split("/extern")[0]
                            return self._process_day_page(page, session, f"{base}/{href}", logger)
                    return False
                
                elif state == "EMPTY_CALENDAR":
                    logger.info("📅 Calendar Empty. Checking next.")
                    return False
                
                # 3. Solve & Submit
                success, code, status = self.solver.solve_from_page(
                    page, 
                    f"GATE_{attempt+1}", 
                    session_age=session.age()
                )
                
                if status == "BLACK_IMAGE":
                    logger.critical("⛔ BLACK CAPTCHA - Session Poisoned")
                    time.sleep(60)
                    return False
                
                if not success or not code:
                    logger.warning(f"❌ OCR failed ({status}) - reloading...")
                    page.reload()
                    continue
                
                logger.info(f"📝 Submitting captcha: '{code}'")
                self.solver.submit_captcha(page, "auto")
                
                # 4. Wait for response
                try:
                    page.wait_for_selector(
                        "div.global-error, a[href*='appointment_showDay'], .error",
                        timeout=8000
                    )
                except: pass
                
                time.sleep(1.0)
            
            logger.error(f"❌ Failed after {max_attempts} attempts")
            return False
            
        except Exception as e:
            logger.error(f"❌ Month page processing error: {e}")
            return False

    def _process_day_page(self, page: Page, session: SessionState, url: str, logger) -> bool:
        """Handle Day Page: Scan Slots -> Navigate Form"""
        try:
            logger.info("📆 Analyzing Day Page...")
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            
            slot_links = page.locator("a.arrow[href*='appointment_showForm']").all()
            if not slot_links:
                logger.info("⚠️ Days shown but no slots active.")
                return False
                
            logger.critical(f"⏰ {len(slot_links)} SLOTS FOUND! ENGAGING!")
            self.global_stats.slots_found += len(slot_links)
            
            target_slot = slot_links[0]
            slot_href = target_slot.get_attribute("href")
            
            if slot_href:
                base_domain = self.base_url.split("/extern")[0]
                form_url = f"{base_domain}/{slot_href}" if not slot_href.startswith("http") else slot_href
                return self._process_booking_form(page, session, form_url, logger)
                
            return False
            
        except Exception as e:
            logger.error(f"❌ Day processing error: {e}")
            return False

    def _process_booking_form(self, page: Page, session: SessionState, url: str, logger) -> bool:
        """Handle Booking: Fill -> Captcha -> Smart Submit"""
        try:
            logger.info("📝 Entering Booking Phase...")
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            
            # 1. FAST FILL
            if not self._fill_booking_form(page, session, logger):
                return False
                
            # 2. CAPTCHA SOLVE
            captcha_code = None
            has_captcha, _ = self.solver.safe_captcha_check(page, "FORM")
            if has_captcha:
                success, code, _ = self.solver.solve_form_captcha_with_retry(
                    page, 
                    "BOOKING", 
                    session_age=session.age()
                )
                if not success:
                    logger.warning("❌ Booking Captcha Failed")
                    return False
                captcha_code = code
            
            # 3. DRY RUN CHECK
            if Config.DRY_RUN:
                logger.critical("🛑 DRY RUN TRIGGERED - NOT SUBMITTING!")
                self.debug_manager.save_critical_screenshot(page, "DRY_RUN_SUCCESS", session.worker_id)
                time.sleep(5) 
                return True 
                
            # 4. SMART SUBMIT
            return self._submit_form(page, session, logger, initial_code=captcha_code)
            
        except Exception as e:
            logger.error(f"❌ Booking error: {e}")
            return False
    
    # ==================== Main Entry Point ====================
    
    def run(self) -> bool:
        """
        Main Execution Entry Point (PRODUCTION MODE)
        Supports both Single Session (AUTO) and Multi-Session (Orchestrated)
        """
        logger.info("=" * 70)
        logger.info(f"[ELITE SNIPER V{self.VERSION}] - STARTING ENGINE")
        logger.info(f"[MODE] {Config.EXECUTION_MODE} | [THREADS] {3 if Config.EXECUTION_MODE == 'AUTO' else 1}")
        logger.info(f"[ATTACK TIME] {Config.ATTACK_HOUR}:00 AM {Config.TIMEZONE}")
        logger.info("=" * 70)
        
        try:
            # Alert Startup
            send_alert(f"🚀 Sniper V2 Started\nID: {self.session_id}\nMode: {Config.EXECUTION_MODE}")
            
            with sync_playwright() as p:
                # Launch Browser (Single instance shared among workers)
                browser = p.chromium.launch(
                    headless=Config.HEADLESS,
                    args=Config.BROWSER_ARGS,
                    timeout=60000
                )
                self.browser = browser
                logger.info("[BROWSER] Launched successfully")

                # ---------------------------------------------------------
                # MULTI-SESSION ORCHESTRATION (The Missing Part)
                # ---------------------------------------------------------
                if Config.EXECUTION_MODE == "AUTO" or Config.EXECUTION_MODE == "HYBRID":
                    logger.info("⚔️  ENGAGING MULTI-SESSION MODE (1 Scout + 2 Attackers)")
                    
                    threads = []
                    # Create 3 Workers
                    for i in range(1, 4):
                        t = Thread(target=self.session_worker, args=(browser, i), name=f"Worker-{i}")
                        t.daemon = True
                        threads.append(t)
                    
                    # Start Threads
                    for t in threads:
                        t.start()
                        time.sleep(2) # Stagger start to prevent ban
                    
                    # Monitor Loop
                    try:
                        while any(t.is_alive() for t in threads):
                            if self.stop_event.is_set():
                                break
                            time.sleep(1)
                    except KeyboardInterrupt:
                        self.stop_event.set()
                        
                else:
                    # Single Session Mode (Manual/Debug)
                    logger.info("🛡️  ENGAGING SINGLE ROBUST SESSION")
                    self._run_single_session(browser, worker_id=1)

                # ---------------------------------------------------------
                
                # Shutdown Sequence
                self.ntp_sync.stop_background_sync()
                try: browser.close()
                except: pass
                
                # Final Reporting
                if self.global_stats.success:
                    self._handle_success()
                    return True
                else:
                    self._handle_completion()
                    return False
                
        except KeyboardInterrupt:
            logger.info("[STOP] Manual Interruption")
            self.stop_event.set()
            return False
        except Exception as e:
            logger.critical(f"🔥 ENGINE FAILURE: {e}", exc_info=True)
            return False
        finally:
            self.cleanup()_name__ == "__main__":
    sniper = EliteSniperV2()
    success = sniper.run()
    sys.exit(0 if success else 1)