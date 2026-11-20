import os
import re
import io
import cv2
import hashlib
import logging
import sqlite3
import pytesseract
import numpy as np
from datetime import datetime, date
from PIL import Image, ExifTags
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
# =====================================================
# CONFIGURATION
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "transactions.db")
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
MAX_FILE_SIZE = 10 * 1024 * 1024
if os.path.exists(TESSERACT_PATH):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("UPI-Shield")
app = FastAPI(
    title="UPI Shield Advanced Forensic API",
    description="UPI screenshot screening and forensic indicators",
    version="3.7"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)
# =====================================================
# DATABASE
# =====================================================
def get_connection():
    return sqlite3.connect(DB_PATH)
def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT,
                image_hash TEXT,
                scanned_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_transaction_id
            ON transactions(transaction_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_image_hash
            ON transactions(image_hash)
        """)
initialize_database()
# =====================================================
# IMAGE PREPROCESSING
# =====================================================
def preprocess_images(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if gray.shape[1] < 1400:
        scale = 1400 / gray.shape[1]
        gray = cv2.resize(
            gray,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC
        )
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8)
    )
    enhanced = clahe.apply(gray)
    adaptive = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11
    )
    return [gray, enhanced, adaptive]
# =====================================================
# OCR ENGINE
# =====================================================
def run_ocr(image, psm=6):
    data = pytesseract.image_to_data(
        image,
        config=f"--oem 3 --psm {psm}",
        output_type=pytesseract.Output.DICT
    )
    words = []
    confidences = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        try:
            confidence = float(data["conf"][i])
        except (ValueError, TypeError):
            confidence = -1
        if word and confidence >= 0:
            words.append(word)
            if confidence > 0:
                confidences.append(confidence)
    average = (
        sum(confidences) / len(confidences)
        if confidences else 0
    )
    return " ".join(words), average
def extract_text(image):
    candidates = []
    for processed in preprocess_images(image):
        for psm in (6, 11):
            try:
                text, confidence = run_ocr(processed, psm)
                candidates.append({
                    "text": text,
                    "confidence": confidence,
                    "length": len(text)
                })
            except Exception as error:
                logger.warning("OCR failed: %s", error)
    if not candidates:
        return "", 0.0
    best = max(
        candidates,
        key=lambda x: (
            x["length"] * 0.6 +
            x["confidence"] * 0.4
        )
    )
    return best["text"], round(best["confidence"], 2)
# =====================================================
# EXPLICIT FAKE / DEMO MARKER DETECTION
# =====================================================

def detect_explicit_fake_marker(image, extracted_text=""):
    """Look for visible Fake/Sample/Demo labels, including small corner text.

    A clear 'FAKE' watermark is a strong warning. Sample/demo labels are
    returned separately because they may indicate a test image, not fraud.
    """
    detected = set()
    text = (extracted_text or "").lower()

    if re.search(r"\bfake\b", text):
        detected.add("FAKE")
    if re.search(r"\bsample\b", text):
        detected.add("SAMPLE")
    if re.search(r"\bdemo\b", text):
        detected.add("DEMO")

    height, width = image.shape[:2]
    # Small watermark in the upper-right corner is commonly missed by full-page OCR.
    x1 = int(width * 0.62)
    y1 = 0
    x2 = width
    y2 = int(height * 0.25)
    corner = image[y1:y2, x1:x2]

    if corner.size:
        gray = cv2.cvtColor(corner, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(
            gray, None, fx=4, fy=4,
            interpolation=cv2.INTER_CUBIC
        )
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, threshold = cv2.threshold(
            gray, 0, 255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        for candidate in (gray, threshold, 255 - threshold):
            try:
                corner_text = pytesseract.image_to_string(
                    candidate,
                    config="--oem 3 --psm 11"
                ).lower()
                if re.search(r"\bfake\b", corner_text):
                    detected.add("FAKE")
                if re.search(r"\bsample\b", corner_text):
                    detected.add("SAMPLE")
                if re.search(r"\bdemo\b", corner_text):
                    detected.add("DEMO")
            except Exception as error:
                logger.warning("Corner watermark OCR failed: %s", error)

    return sorted(detected)


# =====================================================
# PAYMENT EXTRACTION
# =====================================================
def identify_app(text):
    """Identify common UPI apps despite OCR spacing variations."""
    value = re.sub(r"[^a-z0-9]", "", (text or "").lower())

    if "phonepe" in value or "phonepay" in value:
        return "PhonePe"
    if "googlepay" in value or "gpay" in value:
        return "Google Pay"
    if "paytm" in value:
        return "Paytm"
    if "bharatpe" in value:
        return "BharatPe"

    return "UNKNOWN"


def extract_amount(text):
    """Extract currency amounts while avoiding arbitrary ID/date numbers."""
    if not text:
        return None

    patterns = [
        r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"\bamount\s*[:#-]?\s*(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"\b(?:you\s+paid|paid|sent|transferred|total)\s+(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"\b(?:debited|credited)\s+(?:by|with)?\s*(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            amount = float(match.group(1).replace(",", ""))
            if 0 < amount <= 100000000:
                return str(int(amount)) if amount.is_integer() else str(amount)
        except (ValueError, TypeError):
            continue

    return None

def extract_transaction_id(text):
    if not text:
        return None
    patterns = [
        r"Google\s+transaction\s+ID\s*[:#-]?\s*([A-Za-z0-9]{8,40})",
        r"UPI\s+transaction\s+ID\s*[:#-]?\s*([A-Za-z0-9]{8,40})",
        r"transaction\s+ID\s*[:#-]?\s*([A-Za-z0-9]{8,40})",
        r"reference\s+ID\s*[:#-]?\s*([A-Za-z0-9]{8,40})",
        r"(?:UTR|RRN)\s*(?:number|no\.?)?\s*[:#-]?\s*([A-Za-z0-9]{8,40})"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None
def extract_upi_id(text):
    if not text:
        return None
    pattern = r"\b[A-Za-z0-9._-]{2,80}@[A-Za-z0-9.-]{2,40}\b"
    ignored = (
        "gmail.com",
        "yahoo.com",
        "outlook.com"
    )
    for candidate in re.findall(pattern, text):
        if not any(
            domain in candidate.lower()
            for domain in ignored
        ):
            return candidate
    return None
def detect_payment_status(text):
    """Recognize common success/failure wording from UPI screenshots."""
    if not text:
        return "STATUS_NOT_FOUND"

    # Normalize OCR spacing and punctuation while retaining useful words.
    value = re.sub(r"\s+", " ", text.lower()).strip()

    failed = (
        r"\bfailed\b",
        r"\bfailure\b",
        r"\bdeclined\b",
        r"\bunsuccessful\b",
        r"\bnot successful\b",
        r"\bcould not be completed\b",
        r"\bwasn.t completed\b"
    )

    pending = (
        r"\bpending\b",
        r"\bprocessing\b",
        r"\bawaiting confirmation\b",
        r"\bwill be updated\b"
    )

    # Common wording used by Google Pay, PhonePe, Paytm and bank UPI receipts.
    success = (
        r"\bcompleted\b",
        r"\bsuccessfully paid\b",
        r"\bsuccessful\b",
        r"\bpayment successful\b",
        r"\bpaid successfully\b",
        r"\bpayment completed\b",
        r"\btransaction successful\b",
        r"\btransaction is successful\b",
        r"\byou paid\b",
        r"\bpaid to\b",
        r"\bsent to\b",
        r"\bsent successfully\b",
        r"\bmoney sent\b",
        r"\btransferred to\b",
        r"\bpayment sent\b",
        r"\bpaid[.!]?$"
    )

    # Failure/pending take precedence over success wording.
    if any(re.search(pattern, value) for pattern in failed):
        return "FAILED"

    if any(re.search(pattern, value) for pattern in pending):
        return "PENDING"

    if any(re.search(pattern, value) for pattern in success):
        return "SUCCESS"

    return "STATUS_NOT_FOUND"

def check_timestamp(text):
    if not text:
        return False, None
    patterns = [
        r"\b\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b",
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"
    ]
    formats = (
        "%d %b %Y",
        "%d %B %Y",
        "%d %b, %Y",
        "%d %B, %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%Y/%m/%d"
    )
    for pattern in patterns:
        for value in re.findall(pattern, text, re.IGNORECASE):
            value = re.sub(r"\s+", " ", value.strip())
            for fmt in formats:
                try:
                    detected = datetime.strptime(value, fmt).date()
                    return (
                        detected > date.today(),
                        detected.strftime("%d %b %Y")
                    )
                except ValueError:
                    continue
    return False, None
# =====================================================
# FORENSIC ANALYSIS
# =====================================================
def analyze_metadata(pil_image):
    exif_present = False
    software_value = None
    try:
        exif = pil_image.getexif()
        if exif:
            exif_present = True
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag, tag) == "Software":
                    software_value = str(value)
    except Exception:
        pass
    return {
        "format": pil_image.format,
        "width": pil_image.width,
        "height": pil_image.height,
        "mode": pil_image.mode,
        "exif_present": exif_present,
        "software_tag_present": software_value is not None,
        "software_value": software_value
    }
def analyze_ela(pil_image):
    try:
        if pil_image.format != "JPEG":
            return {
                "available": False,
                "mean_difference": None,
                "max_difference": None,
                "interpretation": "ELA unavailable for this format."
            }
        original = pil_image.convert("RGB")
        buffer = io.BytesIO()
        original.save(buffer, format="JPEG", quality=90)
        buffer.seek(0)
        recompressed = Image.open(buffer).convert("RGB")
        a = np.asarray(original).astype(np.int16)
        b = np.asarray(recompressed).astype(np.int16)
        diff = np.abs(a - b)
        return {
            "available": True,
            "mean_difference": round(float(np.mean(diff)), 3),
            "max_difference": int(np.max(diff)),
            "interpretation": (
                "ELA differences are available for review. "
                "They are not proof of manipulation."
            )
        }
    except Exception as error:
        return {
            "available": False,
            "error": str(error),
            "interpretation": "ELA analysis could not be completed."
        }
def analyze_image_quality(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(
        gray,
        cv2.CV_64F
    ).var()
    brightness = float(np.mean(gray))
    if sharpness < 40:
        quality = "LOW_SHARPNESS"
    elif brightness < 30 or brightness > 225:
        quality = "POOR_LIGHTING"
    else:
        quality = "NORMAL"
    return {
        "sharpness_score": round(float(sharpness), 2),
        "brightness": round(brightness, 2),
        "image_quality": quality,
        "interpretation": (
            "Image quality measurements cannot establish authenticity."
        )
    }
def analyze_forensics(pil_image, cv_image):
    return {
        "metadata": analyze_metadata(pil_image),
        "ela": analyze_ela(pil_image),
        "image_quality": analyze_image_quality(cv_image),
        "font_forensics": {
            "status": "NOT_IMPLEMENTED",
            "note": "No trained font consistency model is installed."
        },
        "copy_move_detection": {
            "status": "NOT_IMPLEMENTED",
            "note": "No validated copy-move detector is installed."
        },
        "pixel_manipulation": {
            "status": "NOT_CONFIRMED",
            "note": "Basic image statistics cannot confirm manipulation."
        }
    }
# =====================================================
# DUPLICATE DETECTION
# =====================================================
def check_duplicates(transaction_id, image_hash):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        duplicate_transaction = False
        duplicate_image = False
        if transaction_id:
            cursor.execute(
                """
                SELECT 1 FROM transactions
                WHERE transaction_id=?
                LIMIT 1
                """,
                (transaction_id,)
            )
            duplicate_transaction = cursor.fetchone() is not None
        cursor.execute(
            """
            SELECT 1 FROM transactions
            WHERE image_hash=?
            LIMIT 1
            """,
            (image_hash,)
        )
        duplicate_image = cursor.fetchone() is not None
        cursor.execute(
            """
            INSERT INTO transactions
            (transaction_id, image_hash, scanned_at)
            VALUES (?, ?, ?)
            """,
            (
                transaction_id,
                image_hash,
                datetime.now().isoformat()
            )
        )
        conn.commit()
        return duplicate_transaction, duplicate_image
    finally:
        conn.close()
# =====================================================
# UPDATED RISK ENGINE VERSION 3.3
# =====================================================
def calculate_risk(
    app_name, amount, transaction_id, upi_id, detected_date,
    payment_status, future_date, duplicate_transaction, duplicate_image,
    ocr_confidence, explicit_markers=None
):
    """Rule-based screenshot screening; not proof of fraud or payment."""
    risk_score = 0
    reasons = []
    risk_breakdown = []
    explicit_markers = explicit_markers or []

    def add_risk(points, message):
        nonlocal risk_score
        risk_score += points
        risk_breakdown.append({"reason": message, "points": points})
        reasons.append(message)

    # Duplicate scans are informational only. A real receipt may be
    # uploaded more than once during testing; duplication is not proof of fraud.
    if duplicate_transaction:
        reasons.append("Transaction ID was scanned previously; this alone is not fraud evidence.")
    if duplicate_image:
        reasons.append("This exact image was scanned previously; this alone is not fraud evidence.")

    # A visible FAKE watermark is a strong explicit warning.
    if "FAKE" in explicit_markers:
        add_risk(90, "Explicit FAKE watermark detected in the screenshot.")
    elif "SAMPLE" in explicit_markers or "DEMO" in explicit_markers:
        add_risk(25, "Sample/demo label detected; this may be a test image.")

    # Stronger transaction-status/date warnings.
    if future_date:
        add_risk(40, "Detected transaction date is in the future.")
    if payment_status == "FAILED":
        add_risk(45, "Screenshot text indicates a failed payment.")
    elif payment_status == "PENDING":
        add_risk(15, "Screenshot text indicates a pending payment.")

    # Missing OCR fields are limited penalties, not automatic fake verdicts.
    if not amount:
        add_risk(8, "Payment amount could not be extracted.")
    if not transaction_id:
        add_risk(8, "Transaction ID could not be extracted.")
    if not detected_date:
        add_risk(5, "Transaction date could not be extracted.")
    if payment_status == "STATUS_NOT_FOUND":
        add_risk(10, "Payment status could not be identified.")
    if app_name == "UNKNOWN":
        add_risk(8, "Payment application could not be identified.")
    if ocr_confidence < 30:
        add_risk(15, "OCR confidence is low; manual review is recommended.")
    if not upi_id:
        reasons.append("UPI ID could not be extracted.")

    risk_score = min(int(risk_score), 100)

    success_indicators_present = (
        payment_status == "SUCCESS"
        and app_name != "UNKNOWN"
        and transaction_id is not None
        and ocr_confidence >= 30
        and not future_date
    )

    # Red is reserved for strong negative evidence, not duplicate uploads.
    if "FAKE" in explicit_markers or risk_score >= 60 or (payment_status == "FAILED" and future_date):
        verdict = "SUSPICIOUS"
        risk_level = "HIGH"
        risk_color = "red"
        verdict_label = "HIGH RISK - VERIFY PAYMENT"
    elif success_indicators_present and risk_score < 35:
        verdict = "LOW_RISK"
        risk_level = "LOW"
        risk_color = "green"
        verdict_label = "LOW RISK - SCREENSHOT INDICATORS CONSISTENT"
    else:
        verdict = "INCONCLUSIVE"
        risk_level = "MEDIUM"
        risk_color = "orange"
        verdict_label = "UNABLE TO VERIFY - CHECK MANUALLY"

    if not reasons:
        reasons.append("No major automated screening warning was detected.")

    return (
        verdict, risk_score, reasons, risk_breakdown,
        risk_level, risk_color, verdict_label
    )

# API ROUTES
# =====================================================
@app.get("/")
def home():
    # Serve the existing dashboard as the home page of this same web app.
    return FileResponse(os.path.join(BASE_DIR, "frontend", "index.html"))
@app.get("/health")
def health():
    try:
        ocr_available = bool(
            pytesseract.get_tesseract_version()
        )
    except Exception:
        ocr_available = False
    return {
        "status": "healthy",
        "ocr_available": ocr_available
    }
# =====================================================
# MAIN ANALYSIS ENDPOINT
# =====================================================
@app.post("/api/analyze")
async def analyze_payment(file: UploadFile = File(...)):
    allowed_types = (
        "image/jpeg",
        "image/png",
        "image/webp"
    )
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Only JPEG, PNG and WEBP images are supported."
        )
    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=400,
            detail="Uploaded image is empty."
        )
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Image size must be below 10 MB."
        )
    image_hash = hashlib.sha256(contents).hexdigest()
    try:
        source_image = Image.open(io.BytesIO(contents))
        source_image.load()
        original_format = source_image.format
        if original_format not in ("JPEG", "PNG", "WEBP"):
            raise HTTPException(
                status_code=400,
                detail="Unsupported image format."
            )
        if source_image.width < 100 or source_image.height < 100:
            raise HTTPException(
                status_code=400,
                detail="Image resolution is too small."
            )
        width, height = source_image.size
        pil_image = source_image.convert("RGB")
        pil_image.format = original_format
        cv_image = cv2.cvtColor(
            np.array(pil_image),
            cv2.COLOR_RGB2BGR
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid or corrupted image."
        )
    try:
        extracted_text, ocr_confidence = extract_text(cv_image)
    except Exception as error:
        logger.exception("OCR processing failed")
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(error)}"
        )
    # Extract payment details.
    app_name = identify_app(extracted_text)
    amount = extract_amount(extracted_text)
    transaction_id = extract_transaction_id(
        extracted_text
    )
    upi_id = extract_upi_id(extracted_text)
    payment_status = detect_payment_status(
        extracted_text
    )
    future_date, detected_date = check_timestamp(
        extracted_text
    )
    # Scan the whole OCR text and upper-right corner for explicit labels.
    explicit_markers = detect_explicit_fake_marker(
        cv_image,
        extracted_text
    )

    # Duplicate checks.
    duplicate_transaction, duplicate_image = check_duplicates(
        transaction_id,
        image_hash
    )
    # Forensic analysis.
    forensic_results = analyze_forensics(
        pil_image,
        cv_image
    )
    # Calculate risk.
    (
        verdict, risk_score, reasons, risk_breakdown,
        risk_level, risk_color, verdict_label
    ) = calculate_risk(
        app_name=app_name,
        amount=amount,
        transaction_id=transaction_id,
        upi_id=upi_id,
        detected_date=detected_date,
        payment_status=payment_status,
        future_date=future_date,
        duplicate_transaction=duplicate_transaction,
        duplicate_image=duplicate_image,
        ocr_confidence=ocr_confidence,
        explicit_markers=explicit_markers
    )
    logger.info(
        "Analysis completed: verdict=%s risk=%s",
        verdict,
        risk_score
    )
    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_color": risk_color,
        "verdict_label": verdict_label,
        "risk_breakdown": risk_breakdown,
        "explicit_markers": explicit_markers,
        "payment_details": {
            "app": app_name,
            "amount": amount,
            "transaction_id": transaction_id,
            "upi_id": upi_id,
            "date": detected_date,
            "payment_status": payment_status
        },
        "image_details": {
            "width": width,
            "height": height,
            "format": original_format
        },
        "ocr_confidence": ocr_confidence,
        "checks": {
            "ocr_completed": True,
            "duplicate_transaction_id": duplicate_transaction,
            "duplicate_image": duplicate_image,
            "future_timestamp": future_date,
            "metadata_analysis": True,
            "ela_analysis": forensic_results["ela"]["available"],
            "font_forensics": "NOT_IMPLEMENTED",
            "copy_move_detection": "NOT_IMPLEMENTED",
            "pixel_manipulation": "NOT_CONFIRMED"
        },
        "forensics": forensic_results,
        "reasons": reasons,
        "extracted_text": extracted_text[:3000],
        "disclaimer": (
            "UPI Shield performs automated screenshot screening. "
            "Risk score is a screening indicator, not a probability "
            "of fraud. Low risk does not prove a genuine payment. "
            "A screenshot cannot confirm that money was transferred. "
            "Verify payment using the actual bank or UPI transaction record."
        )
    }
# =====================================================
# STARTUP
# =====================================================
@app.on_event("startup")
def startup_event():
    logger.info("UPI Shield Advanced API v3.7 started.")
    logger.info("Database: %s", DB_PATH)
    logger.info("Tesseract: %s", TESSERACT_PATH)


# Serve the website from the same FastAPI server (single web app).
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
