"""
Elite Sniper v2.0 - Configuration Module
Production Ready - High Frequency Trading Edition
"""

import os
from dotenv import load_dotenv

# Load env vars
load_dotenv()
load_dotenv("config.env")

class Config:
    """Centralized configuration for Elite Sniper v2.0"""
    
    # ==================== Identity & Auth ====================
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    
    # ==================== Captcha Strategy ====================
    # Options: LOCAL, CAPSOLVER, CAPMONSTER, 2CAPTCHA
    # In Production, CAPSOLVER is recommended for speed.
    CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "CAPSOLVER").upper()
    CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")
    
    # Manual Fallback (Only used if Service Fails in Hybrid Mode)
    MANUAL_CAPTCHA_ENABLED = os.getenv("MANUAL_CAPTCHA", "true").lower() == "true"
    MANUAL_CAPTCHA_TIMEOUT = int(os.getenv("MANUAL_CAPTCHA_TIMEOUT", "45"))  # Reduced timeout
    
    # ==================== Applicant Data ====================
    LAST_NAME = os.getenv("LAST_NAME")
    FIRST_NAME = os.getenv("FIRST_NAME")
    EMAIL = os.getenv("EMAIL")
    PASSPORT = os.getenv("PASSPORT")
    PHONE = os.getenv("PHONE")
    
    # ==================== Target ====================
    TARGET_URL = os.getenv("TARGET_URL")
    TIMEZONE = "Asia/Aden"  # GMT+3
    
    # ==================== Session Logic ====================
    # Optimized for High-Competition
    SESSION_MAX_AGE = 240         # 4 minutes max (Fresh sessions are faster)
    SESSION_MAX_IDLE = 10         # Refresh quickly if idle
    HEARTBEAT_INTERVAL = 5        # Aggressive keep-alive
    MAX_CAPTCHA_ATTEMPTS = 5      
    MAX_CONSECUTIVE_ERRORS = 3    
    
    # ==================== Booking Meta ====================
    PURPOSE = os.getenv("PURPOSE", "study")
    
    # ==================== Attack Timing ====================
    ATTACK_HOUR = 2               
    PRE_ATTACK_MINUTE = 59        
    PRE_ATTACK_SECOND = 45        # Start waking up 15s before
    ATTACK_WINDOW_MINUTES = 2     
    
    # ==================== Stealth & Speed ====================
    # Aggressive timing for production
    PATROL_SLEEP_MIN = 5.0        
    PATROL_SLEEP_MAX = 10.0       
    WARMUP_SLEEP = 2.0            
    ATTACK_SLEEP_MIN = 0.1        # Near-instant retry in attack mode
    ATTACK_SLEEP_MAX = 0.5        
    PRE_ATTACK_SLEEP = 0.1        
    
    # ==================== Keywords ====================
    TARGET_KEYWORDS = [
        "Yemeni national",       
        "national language",     
        "student visa",          
        "Student",               
        "Studium",               
        "Sprachkurs",            
        "University",            
        "Course"                 
    ]
    
    # ==================== Infrastructure ====================
    NTP_SERVERS = ["pool.ntp.org", "time.google.com"]
    NTP_SYNC_INTERVAL = 300
    
    HEADLESS = True
    BROWSER_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--disable-extensions",
        "--disable-infobars"
    ]
    
    EVIDENCE_DIR = "evidence"
    MAX_EVIDENCE_AGE_HOURS = 24

    # ==================== Runtime ====================
    DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
    # Force AUTO in high competition if not specified
    EXECUTION_MODE = os.getenv("EXECUTION_MODE", "AUTO").upper()
    
    # Proxies (Structure only)
    PROXIES = []