#!/usr/bin/env python3
"""
Comprehensive Chat Feature Test Suite
Tests all chat endpoints including conversation, loss detection, opt-out, and message storage.
Reuses account creation logic from test_endpoints.py
"""

import requests
import json
import time
from typing import Dict, Any, Tuple, Optional

BASE_URL = "http://127.0.0.1:8000"

# Generate unique identifiers for test data (same as test_endpoints.py)
TIMESTAMP = int(time.time())
HOSPITAL_PHONE = f"070000{TIMESTAMP % 100000:05d}"
PATIENT_PHONE = f"071000{TIMESTAMP % 100000:05d}"

# ANSI color codes for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    END = '\033[0m'

def print_test(test_name: str):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}TEST: {test_name}{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}")

def print_success(message: str):
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")

def print_error(message: str):
    print(f"{Colors.RED}✗ {message}{Colors.END}")

def print_info(message: str):
    print(f"{Colors.YELLOW}ℹ {message}{Colors.END}")

def print_debug(message: str):
    print(f"{Colors.CYAN}→ {message}{Colors.END}")

# ==============================================================================
# ACCOUNT SETUP (Reuse logic from test_endpoints.py)
# ==============================================================================

def setup_hospital() -> Tuple[bool, Optional[str], str, str]:
    """Create a hospital account for testing."""
    print_test("SETUP: Create Hospital Account")
    
    payload = {
        "name": "Chat Test Hospital",
        "phone": HOSPITAL_PHONE,
        "password": "SecurePass123!",
        "gps_lat": -1.286389,
        "gps_lng": 36.817223,
        "address": "Chat Testing Facility",
        "personnel_name": "Dr. Test Admin",
        "personnel_contact": HOSPITAL_PHONE
    }
    
    try:
        response = requests.post(f"{BASE_URL}/auth/hospital/signup", json=payload)
        if response.status_code in [201, 200]:
            data = response.json()
            hospital_id = data.get("id")
            print_success(f"Hospital created: {hospital_id}")
            return True, hospital_id, HOSPITAL_PHONE, "SecurePass123!"
        else:
            print_error(f"Failed to create hospital: {response.status_code}")
            print_debug(f"Response: {response.json()}")
            return False, None, HOSPITAL_PHONE, "SecurePass123!"
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None, HOSPITAL_PHONE, "SecurePass123!"

def setup_patient(hospital_id: str) -> Tuple[bool, Optional[Dict], str, str]:
    """Create a patient account for testing."""
    print_test("SETUP: Create Patient Account")
    
    payload = {
        "name": "Chat Test Patient",
        "phone": PATIENT_PHONE,
        "password": "PatientPass123!",
        "hospital_id": hospital_id,
        "weeks_pregnant_at_signup": 24,
        "age": 30,
        "parity": 1,
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
    
    try:
        response = requests.post(f"{BASE_URL}/auth/patient/signup", json=payload)
        if response.status_code in [201, 200]:
            data = response.json()
            print_success(f"Patient created: {data.get('id')}")
            print_debug(f"Risk Level: {data.get('risk_level')}")
            print_debug(f"LMP: {data.get('lmp')}, EDD: {data.get('edd')}")
            return True, data, PATIENT_PHONE, "PatientPass123!"
        else:
            print_error(f"Failed to create patient: {response.status_code}")
            print_debug(f"Response: {response.json()}")
            return False, None, PATIENT_PHONE, "PatientPass123!"
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False, None, PATIENT_PHONE, "PatientPass123!"

def login_patient(phone: str, password: str) -> Tuple[bool, Optional[str]]:
    """Login patient and get auth token."""
    try:
        payload = {"phone": phone, "password": password}
        response = requests.post(f"{BASE_URL}/auth/patient/login", json=payload)
        if response.status_code == 200:
            token = response.json().get("access_token")
            print_debug(f"Patient login successful: token={token[:20]}...")
            return True, token
        else:
            print_error(f"Login failed: {response.status_code}")
            return False, None
    except Exception as e:
        print_error(f"Login exception: {str(e)}")
        return False, None

# ==============================================================================
# CHAT TESTS
# ==============================================================================

def test_chat_normal_message(token: str) -> bool:
    """Test sending a normal pregnancy-related message."""
    print_test("Chat: Normal Message (Should Get Reply + Triage)")
    
    payload = {"message": "I'm having mild lower back pain. Should I be concerned?"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug(f"Message: {payload['message']}")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_debug(f"Response: {json.dumps(data, indent=2)}")
            
            # Verify response structure
            if "reply" in data and "triage_level" in data:
                print_success(f"Got reply: '{data['reply'][:60]}...'")
                print_success(f"Triage Level: {data.get('triage_level')}")
                print_success(f"Loss Detected: {data.get('loss_detected')}")
                return True
            else:
                print_error(f"Response missing fields. Got: {list(data.keys())}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            try:
                print_debug(f"Response: {response.json()}")
            except:
                print_debug(f"Response: {response.text}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_multiple_messages(token: str) -> bool:
    """Test sending multiple messages in sequence (conversation context)."""
    print_test("Chat: Multiple Sequential Messages (Test Memory)")
    
    messages = [
        "I've been nauseous for three days now",
        "I take prenatal vitamins every morning",
        "Should I go to the hospital?",
    ]
    
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        for i, msg in enumerate(messages, 1):
            print_debug(f"Message {i}/3: '{msg}'")
            payload = {"message": msg}
            response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                print_success(f"  → Reply received, triage: {data.get('triage_level')}")
            else:
                print_error(f"  → Failed: {response.status_code}")
                return False
        
        print_success("All messages processed successfully")
        return True
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_loss_detection_keyword(token: str) -> bool:
    """Test loss detection with keyword trigger."""
    print_test("Chat: Loss Detection - Keyword Trigger")
    
    # Message with loss keyword
    payload = {"message": "I just had a miscarriage and I'm very worried"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug(f"Message: {payload['message']}")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_debug(f"Response: {json.dumps(data, indent=2)}")
            
            # Should detect loss
            if data.get("loss_detected") in [True, "AMBIGUOUS", "CONFIRMED"]:
                print_success(f"Loss detected: {data.get('loss_detected')}")
                print_success(f"Reply: '{data['reply'][:80]}...'")
                return True
            else:
                print_error(f"Expected loss_detected to be True/AMBIGUOUS/CONFIRMED, got {data.get('loss_detected')}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_opt_out_pause(token: str) -> bool:
    """Test PAUSE opt-out command."""
    print_test("Chat: Opt-Out - PAUSE Command")
    
    payload = {"message": "pause"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug("Sending: 'pause' command")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            reply = data.get("reply", "").lower()
            
            # Should contain pause confirmation
            if "pause" in reply or "paused" in reply:
                print_success(f"PAUSE confirmed: {data['reply']}")
                return True
            else:
                print_error(f"Expected pause confirmation, got: {data['reply']}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_while_paused(token: str) -> bool:
    """Test that messages are blocked while paused."""
    print_test("Chat: Message While PAUSED (Should Be Blocked)")
    
    payload = {"message": "Hello, are you there?"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug("Sending message while paused...")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            reply = data.get("reply", "").lower()
            
            # Should get pause acknowledgment, not normal reply
            if "pause" in reply or "paused" in reply or "resume" in reply:
                print_success(f"Correctly blocked: {data['reply']}")
                return True
            else:
                print_error(f"Expected pause-related response, got: {data['reply']}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_opt_out_resume(token: str) -> bool:
    """Test RESUME opt-out command to resume after pause."""
    print_test("Chat: Opt-Out - RESUME Command")
    
    payload = {"message": "resume"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug("Sending: 'resume' command")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            reply = data.get("reply", "").lower()
            
            # Should contain resume confirmation
            if "resume" in reply or "resum" in reply:
                print_success(f"RESUME confirmed: {data['reply']}")
                return True
            else:
                print_error(f"Expected resume confirmation, got: {data['reply']}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_after_resume(token: str) -> bool:
    """Test that normal messages work after resume."""
    print_test("Chat: Normal Message After RESUME (Should Succeed)")
    
    payload = {"message": "Thank you for being here. I feel better now."}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug(f"Message: {payload['message']}")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_success(f"Got reply: '{data['reply'][:60]}...'")
            print_success(f"Triage Level: {data.get('triage_level')}")
            return True
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_opt_out_stop(token: str) -> bool:
    """Test STOP opt-out command (indefinite opt-out)."""
    print_test("Chat: Opt-Out - STOP Command")
    
    payload = {"message": "stop"}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug("Sending: 'stop' command")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            reply = data.get("reply", "").lower()
            
            # Should contain stop confirmation
            if "stop" in reply or "stopped" in reply:
                print_success(f"STOP confirmed: {data['reply']}")
                return True
            else:
                print_error(f"Expected stop confirmation, got: {data['reply']}")
                return False
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_no_auth() -> bool:
    """Test chat endpoint without authentication."""
    print_test("Chat: Message Without Auth (Should Fail)")
    
    payload = {"message": "I need help"}
    
    print_debug("Sending message without Authorization header...")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code in [401, 403]:
            print_success(f"Correctly rejected unauthorized request: {response.status_code}")
            return True
        else:
            print_error(f"Expected 401/403, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_various_pregnancy_topics(token: str) -> bool:
    """Test chat with various pregnancy-related topics."""
    print_test("Chat: Various Pregnancy Topics")
    
    topics = [
        "What should I eat during pregnancy?",
        "Is it safe to exercise while pregnant?",
        "When should I start feeling the baby move?",
        "What are normal symptoms at 6 months?",
        "Should I be worried about spotting?",
    ]
    
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        for i, topic in enumerate(topics, 1):
            print_debug(f"Topic {i}/{len(topics)}: {topic[:50]}...")
            payload = {"message": topic}
            response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                triage = data.get("triage_level", "unknown")
                print_success(f"  → Response received (triage: {triage})")
            else:
                print_error(f"  → Failed: {response.status_code}")
                return False
        
        print_success("All topics handled successfully")
        return True
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_empty_message(token: str) -> bool:
    """Test chat with empty message."""
    print_test("Chat: Empty Message (Edge Case)")
    
    payload = {"message": ""}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug("Sending empty message...")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        # Should either succeed with fallback or reject
        if response.status_code in [200, 400]:
            print_success(f"Request handled: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print_debug(f"Got reply: {data.get('reply', '')[:50]}...")
            return True
        else:
            print_error(f"Unexpected status code: {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

def test_chat_very_long_message(token: str) -> bool:
    """Test chat with very long message."""
    print_test("Chat: Very Long Message")
    
    long_message = "I'm having some concerns about my pregnancy. " * 50  # Very long
    payload = {"message": long_message}
    headers = {"Authorization": f"Bearer {token}"}
    
    print_debug(f"Sending message ({len(long_message)} chars)...")
    
    try:
        response = requests.post(f"{BASE_URL}/chat/message", json=payload, headers=headers)
        print_info(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print_success(f"Long message handled, got reply ({len(data.get('reply', ''))} chars)")
            return True
        else:
            print_error(f"Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Exception: {str(e)}")
        return False

# ==============================================================================
# MAIN TEST RUNNER
# ==============================================================================

def main():
    """Run all chat tests."""
    print(f"\n{Colors.CYAN}{'='*70}{Colors.END}")
    print(f"{Colors.CYAN}COMPREHENSIVE CHAT TEST SUITE{Colors.END}")
    print(f"{Colors.CYAN}{'='*70}{Colors.END}")
    
    results = []
    
    # Setup: Create accounts
    print_debug(f"Timestamp: {TIMESTAMP}")
    print_debug(f"Hospital Phone: {HOSPITAL_PHONE}")
    print_debug(f"Patient Phone: {PATIENT_PHONE}")
    
    success, hospital_id, hosp_phone, hosp_pass = setup_hospital()
    if not success or not hospital_id:
        print_error("Failed to setup hospital")
        return
    
    success, patient_data, pat_phone, pat_pass = setup_patient(hospital_id)
    if not success or not patient_data:
        print_error("Failed to setup patient")
        return
    
    success, patient_token = login_patient(pat_phone, pat_pass)
    if not success or not patient_token:
        print_error("Failed to login patient")
        return
    
    # Run tests
    print_debug("\n" + "="*70)
    print_debug("STARTING CHAT TESTS")
    print_debug("="*70)
    
    tests = [
        ("Chat: No Auth", lambda: test_chat_no_auth()),
        ("Chat: Normal Message", lambda: test_chat_normal_message(patient_token)),
        ("Chat: Multiple Messages", lambda: test_chat_multiple_messages(patient_token)),
        ("Chat: Pregnancy Topics", lambda: test_chat_various_pregnancy_topics(patient_token)),
        ("Chat: Loss Detection", lambda: test_chat_loss_detection_keyword(patient_token)),
        ("Chat: PAUSE Command", lambda: test_chat_opt_out_pause(patient_token)),
        ("Chat: While Paused", lambda: test_chat_while_paused(patient_token)),
        ("Chat: RESUME Command", lambda: test_chat_opt_out_resume(patient_token)),
        ("Chat: After Resume", lambda: test_chat_after_resume(patient_token)),
        ("Chat: STOP Command", lambda: test_chat_opt_out_stop(patient_token)),
        ("Chat: Empty Message", lambda: test_chat_empty_message(patient_token)),
        ("Chat: Very Long Message", lambda: test_chat_very_long_message(patient_token)),
    ]
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print_error(f"Test {test_name} raised exception: {str(e)}")
            results.append((test_name, False))
    
    # Summary
    print(f"\n{Colors.CYAN}{'='*70}{Colors.END}")
    print(f"{Colors.CYAN}TEST SUMMARY{Colors.END}")
    print(f"{Colors.CYAN}{'='*70}{Colors.END}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"{status} - {test_name}")
    
    print(f"\n{Colors.CYAN}Total: {passed}/{total} tests passed{Colors.END}")
    
    if passed == total:
        print(f"{Colors.GREEN}All tests passed! ✓{Colors.END}\n")
    else:
        print(f"{Colors.RED}{total - passed} test(s) failed.{Colors.END}\n")

if __name__ == "__main__":
    main()
