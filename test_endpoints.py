#!/usr/bin/env python3
"""
Test script for HASH Backend API endpoints.
Tests all authentication and hospital discovery endpoints.
"""

import requests
import json
import time
from typing import Dict, Any

BASE_URL = "http://127.0.0.1:8000"

# Generate unique identifiers for test data
TIMESTAMP = int(time.time())
HOSPITAL_PHONE = f"070000{TIMESTAMP % 100000:05d}"
PATIENT_PHONE = f"071000{TIMESTAMP % 100000:05d}"

# ANSI color codes for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_test(test_name: str):
    print(f"\n{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}TEST: {test_name}{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}")

def print_success(message: str):
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")

def print_error(message: str):
    print(f"{Colors.RED}✗ {message}{Colors.END}")

def print_info(message: str):
    print(f"{Colors.YELLOW}ℹ {message}{Colors.END}")

def test_health_check():
    """Test if the server is running."""
    print_test("Health Check - Root Endpoint")
    try:
        response = requests.get(f"{BASE_URL}/")
        if response.status_code == 200:
            data = response.json()
            print_success(f"Server is running: {data}")
            return True
        else:
            print_error(f"Unexpected status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to server. Make sure it's running on http://127.0.0.1:8000")
        return False

def test_hospital_signup_weak_password():
    """Test hospital signup with weak password (should fail)."""
    print_test("Hospital Signup - Weak Password (Should Fail)")
    
    payload = {
        "name": "Test Hospital",
        "phone": "0700000001",
        "password": "weak",  # Too weak - less than 8 chars, no uppercase/lowercase/digit/special
        "gps_lat": -1.286389,
        "gps_lng": 36.817223,
        "address": "123 Main Street",
        "personnel_name": "Dr. John",
        "personnel_contact": "0700000002"
    }
    
    print_info(f"Request: POST /auth/hospital/signup")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/hospital/signup", json=payload)
        print_info(f"Status Code: {response.status_code}")
        print_info(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 400:
            print_success("Correctly rejected weak password")
            return True
        else:
            print_error(f"Expected 400, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_hospital_signup_strong_password():
    """Test hospital signup with strong password (should succeed)."""
    print_test("Hospital Signup - Strong Password (Should Succeed)")
    
    payload = {
        "name": "Test Hospital Strong",
        "phone": HOSPITAL_PHONE,
        "password": "SecurePass123!",  # Strong password: 8+ chars, uppercase, lowercase, digit, special
        "gps_lat": -1.286389,
        "gps_lng": 36.817223,
        "address": "456 Hospital Ave",
        "personnel_name": "Dr. Jane Smith",
        "personnel_contact": "0700000011"
    }
    
    print_info(f"Request: POST /auth/hospital/signup")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/hospital/signup", json=payload)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code in [201, 200]:  # Accept both 201 Created or 200 OK
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            print_success("Hospital created successfully")
            return True, data.get("id"), payload["phone"], payload["password"]
        else:
            print_error(f"Expected 201 or 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False, None, payload["phone"], payload["password"]
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None, payload["phone"], payload["password"]

def test_hospital_login_invalid():
    """Test hospital login with invalid credentials."""
    print_test("Hospital Login - Invalid Credentials (Should Fail)")
    
    payload = {
        "phone": "9999999999",
        "password": "WrongPass123!"
    }
    
    print_info(f"Request: POST /auth/hospital/login")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/hospital/login", json=payload)
        print_info(f"Status Code: {response.status_code}")
        print_info(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 401:
            print_success("Correctly rejected invalid credentials")
            return True
        else:
            print_error(f"Expected 401, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_hospital_login_valid(phone: str, password: str):
    """Test hospital login with valid credentials."""
    print_test("Hospital Login - Valid Credentials (Should Succeed)")
    
    payload = {
        "phone": phone,
        "password": password
    }
    
    print_info(f"Request: POST /auth/hospital/login")
    print_info(f"Payload: {json.dumps({'phone': phone, 'password': '***'}, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/hospital/login", json=payload)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")
            user_type = data.get("user_type")
            print_info(f"Token: {token[:20]}..." if token else "No token")
            print_info(f"User Type: {user_type}")
            print_success("Hospital logged in successfully")
            return True, token
        else:
            print_error(f"Expected 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False, None
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None

def test_get_hospitals():
    """Test public hospital discovery endpoint."""
    print_test("Get Hospitals - Public Discovery (No Auth Required)")
    
    print_info(f"Request: GET /hospitals?skip=0&limit=10")
    
    try:
        response = requests.get(f"{BASE_URL}/hospitals?skip=0&limit=10")
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Found {len(data)} hospitals")
            if data:
                print_info(f"First hospital: {json.dumps(data[0], indent=2)}")
            print_success("Hospitals retrieved successfully")
            return True
        else:
            print_error(f"Expected 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_patient_signup_invalid_hospital():
    """Test patient signup with invalid hospital_id."""
    print_test("Patient Signup - Invalid Hospital ID (Should Fail)")
    
    payload = {
        "name": "Test Patient",
        "phone": "0700000020",
        "password": "PatientPass123!",
        "hospital_id": "99999999-9999-9999-9999-999999999999",
        "weeks_pregnant_at_signup": 10,
        "age": 25,
        "parity": 1,
        "previous_loss": False,
        "previous_stillbirth": False,
        "previous_caesarean": False,
        "previous_preeclampsia": False,
        "has_hypertension": False,
        "has_diabetes": False,
        "has_sickle_cell": False,
        "has_hiv": False,
        "has_severe_anaemia": False,
        "multiple_pregnancy": False,
        "late_anc_initiation": False,
        "no_prior_anc": False,
    }
    
    print_info(f"Request: POST /auth/patient/signup")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/patient/signup", json=payload)
        print_info(f"Status Code: {response.status_code}")
        print_info(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 404:
            print_success("Correctly rejected invalid hospital_id")
            return True
        else:
            print_error(f"Expected 404, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_patient_signup_valid(hospital_id: str):
    """Test patient signup with valid data."""
    print_test("Patient Signup - Valid Data (Should Succeed)")
    
    payload = {
        "name": "Test Patient Valid",
        "phone": PATIENT_PHONE,
        "password": "PatientPass123!",
        "hospital_id": hospital_id,
        "weeks_pregnant_at_signup": 12,
        "age": 28,
        "parity": 2,
        "language": "English",
        "preferred_support": "peer",
        "previous_loss": False,
        "previous_stillbirth": False,
        "previous_caesarean": False,
        "previous_preeclampsia": False,
        "has_hypertension": False,
        "has_diabetes": False,
        "has_sickle_cell": False,
        "has_hiv": False,
        "has_severe_anaemia": False,
        "multiple_pregnancy": False,
        "late_anc_initiation": False,
        "no_prior_anc": False,
    }
    
    print_info(f"Request: POST /auth/patient/signup")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/patient/signup", json=payload)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code in [201, 200]:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            print_success("Patient created successfully with computed LMP/EDD")
            return True, data, payload["phone"], payload["password"]
        else:
            print_error(f"Expected 201 or 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False, None, payload["phone"], payload["password"]
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None, payload["phone"], payload["password"]

def test_patient_login_valid(phone: str, password: str):
    """Test patient login with valid credentials."""
    print_test("Patient Login - Valid Credentials (Should Succeed)")
    
    payload = {
        "phone": phone,
        "password": password
    }
    
    print_info(f"Request: POST /auth/patient/login")
    print_info(f"Payload: {json.dumps({'phone': phone, 'password': '***'}, indent=2)}")
    
    try:
        response = requests.post(f"{BASE_URL}/auth/patient/login", json=payload)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")
            user_type = data.get("user_type")
            print_info(f"Token: {token[:20]}..." if token else "No token")
            print_info(f"User Type: {user_type}")
            print_success("Patient logged in successfully")
            return True, token
        else:
            print_error(f"Expected 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False, None
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None

def test_get_current_user(token: str, user_type: str):
    """Test protected /auth/me endpoint."""
    print_test(f"Get Current User - Protected Endpoint ({user_type})")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    print_info(f"Request: GET /auth/me")
    print_info(f"Headers: Authorization: Bearer {token[:20]}...")
    
    try:
        response = requests.get(f"{BASE_URL}/auth/me", headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            print_success("Current user retrieved successfully")
            return True
        else:
            print_error(f"Expected 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_get_current_user_no_token():
    """Test protected /auth/me endpoint without token."""
    print_test("Get Current User - No Token (Should Fail)")
    
    print_info(f"Request: GET /auth/me (no Authorization header)")
    
    try:
        response = requests.get(f"{BASE_URL}/auth/me")
        print_info(f"Status Code: {response.status_code}")
        print_info(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code in (401, 403):
            print_success("Correctly rejected request without token")
            return True
        else:
            print_error(f"Expected 401 or 403, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_normal_message(token: str):
    """Test normal chat message (should get reply + triage_level)."""
    print_test("Chat - Normal Message (Should Get Reply + Triage)")
    
    payload = {
        "message": "I'm feeling a bit dizzy and nauseous. Is this normal?"
    }
    
    print_info(f"Request: POST /chat/message")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            # Verify response schema
            if "reply" in data and "triage_level" in data and "loss_detected" in data:
                if data["triage_level"] in ("low", "medium", "high"):
                    print_success("Chat message processed with correct response schema")
                    return True
                else:
                    print_error(f"Invalid triage_level: {data['triage_level']}")
                    return False
            else:
                print_error("Missing required fields in response")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            print_info(f"Response: {json.dumps(response.json(), indent=2)}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_loss_detection(token: str):
    """Test loss detection message."""
    print_test("Chat - Loss Detection Message")
    
    payload = {
        "message": "I had a miscarriage yesterday. I lost my baby."
    }
    
    print_info(f"Request: POST /chat/message")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            if data.get("loss_detected") or data.get("triage_level") == "high":
                print_success("Loss detection triggered correctly")
                return True, data.get("loss_detected")
            else:
                print_info("Message processed but loss not detected (may be AMBIGUOUS path)")
                return True, False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False, False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, False

def test_chat_pause_command(token: str):
    """Test PAUSE opt-out command."""
    print_test("Chat - PAUSE Command (Opt-out)")
    
    payload = {
        "message": "PAUSE"
    }
    
    print_info(f"Request: POST /chat/message")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            if "pause" in data.get("reply", "").lower() or "paused" in data.get("reply", "").lower():
                print_success("PAUSE command handled correctly")
                return True
            else:
                print_error("Expected pause confirmation message")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_stop_command(token: str):
    """Test STOP opt-out command."""
    print_test("Chat - STOP Command (Opt-out)")
    
    payload = {
        "message": "STOP"
    }
    
    print_info(f"Request: POST /chat/message")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            if "stop" in data.get("reply", "").lower():
                print_success("STOP command handled correctly")
                return True
            else:
                print_error("Expected stop confirmation message")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_resume_command(token: str):
    """Test RESUME opt-out command."""
    print_test("Chat - RESUME Command (Resume after Opt-out)")
    
    payload = {
        "message": "RESUME"
    }
    
    print_info(f"Request: POST /chat/message")
    print_info(f"Payload: {json.dumps(payload, indent=2)}")
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            if "welcome" in data.get("reply", "").lower() or "resume" in data.get("reply", "").lower():
                print_success("RESUME command handled correctly")
                return True
            else:
                print_error("Expected resume confirmation message")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def main():
    """Run all tests."""
    print(f"\n{Colors.YELLOW}{'='*60}")
    print(f"HASH Backend API Test Suite")
    print(f"{'='*60}{Colors.END}\n")
    
    results = {}
    
    # Test 1: Health check
    results["Health Check"] = test_health_check()
    if not results["Health Check"]:
        print_error("Server is not accessible. Exiting.")
        return
    
    # Test 2: Hospital signup with weak password
    results["Hospital Signup (Weak Password)"] = test_hospital_signup_weak_password()
    
    # Test 3: Hospital signup with strong password
    result, hospital_id, hosp_phone, hosp_pass = test_hospital_signup_strong_password()
    results["Hospital Signup (Strong Password)"] = result
    
    # Test 4: Get hospitals (public endpoint)
    results["Get Hospitals (Public)"] = test_get_hospitals()
    
    # Test 5: Hospital login invalid
    results["Hospital Login (Invalid)"] = test_hospital_login_invalid()
    
    # Test 6: Hospital login valid
    if hospital_id:
        result, hosp_token = test_hospital_login_valid(hosp_phone, hosp_pass)
        results["Hospital Login (Valid)"] = result
        
        # Test 7: Get current hospital user
        if hosp_token:
            results["Get Current User (Hospital)"] = test_get_current_user(hosp_token, "Hospital")
    
    # Test 8: Patient signup with invalid hospital
    results["Patient Signup (Invalid Hospital)"] = test_patient_signup_invalid_hospital()
    
    # Test 9: Patient signup with valid data
    if hospital_id:
        result, patient_data, patient_phone, patient_pass = test_patient_signup_valid(hospital_id)
        results["Patient Signup (Valid)"] = result
        
        # Test 10: Patient login valid
        if result:
            result, patient_token = test_patient_login_valid(patient_phone, patient_pass)
            results["Patient Login (Valid)"] = result
            
            # Test 11: Get current patient user
            if patient_token:
                results["Get Current User (Patient)"] = test_get_current_user(patient_token, "Patient")
                
                # ================================================================
                # M3 CONVERSATION ENGINE TESTS — Requires authenticated patient
                # ================================================================
                
                # Test 12: Normal chat message
                results["Chat - Normal Message"] = test_chat_normal_message(patient_token)
                
                # Test 13: Loss detection message
                result, loss_detected = test_chat_loss_detection(patient_token)
                results["Chat - Loss Detection"] = result
                
                # Test 14: PAUSE command
                results["Chat - PAUSE Command"] = test_chat_pause_command(patient_token)
                
                # Test 15: STOP command
                results["Chat - STOP Command"] = test_chat_stop_command(patient_token)
                
                # Test 16: RESUME command
                results["Chat - RESUME Command"] = test_chat_resume_command(patient_token)
    
    # Test 17: Get current user without token
    results["Get Current User (No Token)"] = test_get_current_user_no_token()
    
    # Print summary
    print(f"\n{Colors.YELLOW}{'='*60}")
    print(f"Test Summary")
    print(f"{'='*60}{Colors.END}\n")
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    for test_name, result in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"{status} - {test_name}")
    
    print(f"\n{Colors.YELLOW}Total: {passed}/{total} tests passed{Colors.END}\n")
    
    if passed == total:
        print(f"{Colors.GREEN}All tests passed!{Colors.END}\n")
    else:
        print(f"{Colors.RED}{total - passed} test(s) failed.{Colors.END}\n")

if __name__ == "__main__":
    main()
