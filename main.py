import base64
import io
import bcrypt
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fastapi import Form, HTTPException
import fitz  # PyMuPDF
import os
import time
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
import google.generativeai as genai

# ================== CONFIG ======================
MONGO_URI = "mongodb+srv://ayushsuryavanshi03:tSM6nbQBtNkkM8uO@cluster0.i9n9dqa.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "resume_analyzer"
COLLECTION_NAME = "prompts"

# Email configuration (replace with your SMTP details)
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SMTP_USERNAME = 'skillzage91@gmail.com'
SMTP_PASSWORD = 'deqe hnyu xddj'  # This is your 16-character App Password
FROM_EMAIL = 'skillzage91@gmail.com'

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
prompt_collection = db[COLLECTION_NAME]
logs_collection = db["resume_logs"]  # Store logs
users_collection = db["users"]
otp_collection = db["otps"]  # New collection for OTP storage

ADMIN_TOKEN = "drdoom"

genai.configure(api_key="AIzaSyCcoQ40u_iM1BIvp26iLqVTWdHp3Ky0TAw")

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============== OTP FUNCTIONS ==================
def generate_otp(email: str) -> str:
    """Generate a 6-digit OTP and store it in database"""
    otp = str(secrets.randbelow(10**6)).zfill(6)
    expiry_time = datetime.now() + timedelta(minutes=5)  # OTP valid for 5 minutes
    
    otp_collection.update_one(
        {"email": email},
        {"$set": {
            "otp": otp,
            "expiry": expiry_time,
            "verified": False
        }},
        upsert=True
    )
    
    return otp

def send_otp_email(email: str, otp: str) -> bool:
    """Send OTP to user's email"""
    subject = "Your OTP for Resume Analyzer"
    body = f"""
    Your verification code for the AI Resume Analyzer is:
    
    {otp}
    
    This code will expire in 5 minutes.
    """
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = FROM_EMAIL
    msg['To'] = email
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def verify_otp(email: str, user_otp: str) -> bool:
    """Verify if the OTP is correct and not expired"""
    otp_data = otp_collection.find_one({"email": email})
    
    if not otp_data:
        return False
        
    # Check if OTP matches and isn't expired
    if (otp_data['otp'] == user_otp and 
        datetime.now() < otp_data['expiry']):
        otp_collection.update_one(
            {"email": email},
            {"$set": {"verified": True}}
        )
        return True
    
    return False

def is_email_verified(email: str) -> bool:
    """Check if email has been verified"""
    otp_data = otp_collection.find_one({"email": email})
    return otp_data.get('verified', False) if otp_data else False

# =============== OTP ROUTES ====================
class SendOtpRequest(BaseModel):
    email: str

class VerifyOtpRequest(BaseModel):
    email: str
    otp: str

@app.post("/send-otp")
async def send_otp(request: SendOtpRequest):
    email = request.email
    
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    
    # Generate and store OTP
    otp = generate_otp(email)
    
    # Send OTP via email
    if send_otp_email(email, otp):
        return {"status": True, "message": "OTP sent successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send OTP")

@app.post("/verify-otp")
async def verify_otp_route(request: VerifyOtpRequest):
    email = request.email
    otp = request.otp
    
    if not email or not otp:
        raise HTTPException(status_code=400, detail="Email and OTP are required")
    
    if verify_otp(email, otp):
        return {"status": True, "message": "Email verified successfully"}
    else:
        raise HTTPException(status_code=400, detail="Invalid OTP or OTP expired")

def initialize_prompts():
    if prompt_collection.count_documents({}) == 0:
        prompts = [
            {"prompt_id": 1, "prompt_text": "Based on the candidate of a {age}-year-old student pursuing {course} in {specialization}, aiming for a career as a {career_goal}. So give them some suggestion if their resume is for another domain"},
            {"prompt_id": 2, "prompt_text": "Identify skills of the candidate from the following list, suggest improvements to highlight key strengths."},
            {"prompt_id": 3, "prompt_text": "Evaluate resume clarity, structure, and formatting. Point out any issues or improvements to make it more professional."}
        ]
        prompt_collection.insert_many(prompts)

def get_prompts_from_db():
    return [doc["prompt_text"] for doc in prompt_collection.find().sort("prompt_id", 1)]


# =============== MODIFY EXISTING EVALUATE ROUTE ================
@app.post("/evaluate")
async def evaluate_resume(
    base64_pdf: str = Form(...),
    age: str = Form(""),
    course: str = Form(""),
    specialization: str = Form(""),
    career_goal: str = Form(""),
    email: str = Form(...)  # Add email parameter
):
    # Check if email is verified
    if not is_email_verified(email):
        raise HTTPException(status_code=403, detail="Email not verified")
    
    try:
        start_time = time.time()

        pdf_bytes = base64.b64decode(base64_pdf)
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        first_page = pdf_doc[0].get_pixmap()
        img_byte_arr = io.BytesIO(first_page.tobytes("jpeg"))
        image_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
    except Exception as e:
        return {"error": f"PDF processing failed: {str(e)}"}

    prompts = get_prompts_from_db()
    if len(prompts) < 3:
        return {"error": "Not enough prompts in database."}

    try:
        prompt1_template = prompts[0]
        prompt1 = prompt1_template.format(
            age=age,
            course=course,
            specialization=specialization,
            career_goal=career_goal
        )
    except KeyError as e:
        return {"error": f"Missing placeholder in prompt1: {e}"}

    master_prompt = f"""
You are a highly skilled HR professional, career coach, and ATS expert.

1. {prompt1}
2. {prompts[1]}
3. {prompts[2]}
"""

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content([
            "Analyze this resume carefully:",
            {"mime_type": "image/jpeg", "data": image_base64},
            master_prompt
        ])
        response_text = response.text
        end_time = time.time()

        # Save analysis log with email
        logs_collection.insert_one({
            "email": email,
            "age": age,
            "course": course,
            "specialization": specialization,
            "career_goal": career_goal,
            "response_time": round(end_time - start_time, 2),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "success": True
        })
    except Exception as e:
        return {"error": f"Gemini API error: {str(e)}"}

    return {"response": response_text}

# ... (rest of your existing code remains the same)
class PromptUpdate(BaseModel):
    prompt_text: str
    prompt_id: int

@app.post("/update_prompt")
async def update_prompt(data: PromptUpdate, request: Request):
    try:
        result = prompt_collection.update_one(
            {"prompt_id": int(data.prompt_id)},
            {"$set": {"prompt_text": data.prompt_text}}
        )
        if result.modified_count == 1:
            return {"status": True}
        return {"status": False, "error": "Prompt not found or unchanged."}
    except Exception as e:
        return {"status": False, "error": str(e)}

@app.get("/debug_prompts")
async def debug_prompts():
    try:
        prompts = list(prompt_collection.find({}, {"prompt_id": 1, "prompt_text": 1, "_id": 0}))
        return {"prompts": prompts}
    except Exception as e:
        return {"status": False, "error": str(e)}




# ================ INIT ============================
initialize_prompts()
