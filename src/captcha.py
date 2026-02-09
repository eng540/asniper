Improve Captcha Solving Success Rate
Goal
The current ddddocr solver is struggling with the embassy captchas, leading to repeated WRONG_CODE errors. We need to improve the image preprocessing to help the OCR and ensure Manual Mode is available as a fallback.

Proposed Changes
src/captcha.py
[MODIFY] 
captcha.py
Enhance 
_preprocess_image
:
Increase contrast before binarization.
Tune the morphological operations (erosion/dilation) to better remove noise without destroying characters.
Add cv2.resize to upscale the image (2x or 3x) before OCR. Larger text is often easier for ddddocr to read.
src/captcha.py
[MODIFY] 
captcha.py
Enhance 
solve_from_page
:

In AUTO mode, if result is TOO_SHORT, trigger self.reload_captcha(page).
Loop up to max_attempts doing this reload-and-retry.
Verify Replacements Gone:

Ensure replacements dict is removed from 
_clean_ocr_result
 (Confirmed).
Verification Plan
Automated Tests
None.
Manual Verification
Run the bot.
Observe [GATE_X] Captcha solved: '...'.
Check if the rate of WRONG_CODE decreases.
User Notification
Inform the user that the "Invalid Code" issue is due to low OCR accuracy.
Propose the code enhancements.
Remind them they can enable "Manual Mode" in config to solve via Telegram if they prefer reliability over full automation.
