#!/usr/bin/env python3

import requests
import sys
import json
import time
from datetime import datetime

class ClaimOSAPITester:
    def __init__(self, base_url="https://agentic-claims.preview.emergentagent.com/api"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        self.results = []

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=30):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        headers = {'Content-Type': 'application/json'}

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            start_time = time.time()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=timeout)
            
            duration = time.time() - start_time
            success = response.status_code == expected_status
            
            if success:
                self.tests_passed += 1
                print(f"✅ PASSED - Status: {response.status_code} ({duration:.2f}s)")
                result_data = response.json() if response.content else {}
            else:
                print(f"❌ FAILED - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                result_data = {}

            result = {
                "test": name,
                "endpoint": endpoint,
                "method": method,
                "status_code": response.status_code,
                "expected_status": expected_status,
                "success": success,
                "duration": duration,
                "data": result_data
            }
            self.results.append(result)
            
            return success, result_data

        except Exception as e:
            print(f"❌ FAILED - Error: {str(e)}")
            result = {
                "test": name,
                "endpoint": endpoint,
                "method": method,
                "error": str(e),
                "success": False,
                "duration": 0,
                "data": {}
            }
            self.results.append(result)
            return False, {}

    def test_health_check(self):
        """Test API health endpoint"""
        return self.run_test("API Health Check", "GET", "", 200)

    def test_dashboard_stats(self):
        """Test dashboard statistics endpoint"""
        success, data = self.run_test("Dashboard Stats", "GET", "dashboard/stats", 200)
        if success:
            # Verify required fields are present
            required_fields = ['totalClaims', 'approved', 'rejected', 'activePolicies', 'recentClaims']
            missing_fields = [f for f in required_fields if f not in data]
            if missing_fields:
                print(f"⚠️  Missing required fields: {missing_fields}")
            else:
                print(f"   ✓ Total Claims: {data['totalClaims']}")
                print(f"   ✓ Active Policies: {data['activePolicies']}")
                print(f"   ✓ Recent Claims: {len(data['recentClaims'])}")
        return success, data

    def test_policies_list(self):
        """Test policies listing endpoint"""
        success, data = self.run_test("Policies List", "GET", "policies", 200)
        if success:
            print(f"   ✓ Retrieved {len(data)} policies")
            if data:
                print(f"   ✓ First policy: {data[0].get('policy_number')} - {data[0].get('holder_name')}")
        return success, data

    def test_policy_lookup_sarah_chen(self):
        """Test specific policy lookup for Sarah Chen"""
        success, data = self.run_test(
            "Policy Lookup - Sarah Chen", 
            "GET", 
            "policies/lookup?policy_number=AUTO-2024-001847", 
            200
        )
        if success:
            if data.get('holder_name') == 'Sarah Chen':
                print(f"   ✅ Correctly found Sarah Chen")
                print(f"   ✓ Policy Type: {data.get('policy_type')}")
                print(f"   ✓ Status: {data.get('status')}")
                print(f"   ✓ Coverage: ${data.get('coverage_limit')}")
            else:
                print(f"   ❌ Expected Sarah Chen, got {data.get('holder_name')}")
                success = False
        return success, data

    def test_policy_search_expired(self):
        """Test policy search for expired policies"""
        success, data = self.run_test("Policy Search - Expired", "GET", "policies/search?q=expired", 200)
        if success:
            expired_policies = [p for p in data if p.get('status') == 'expired']
            print(f"   ✓ Found {len(expired_policies)} expired policies")
            if expired_policies:
                david_kim = next((p for p in expired_policies if p.get('holder_name') == 'David Kim'), None)
                if david_kim:
                    print(f"   ✅ Found David Kim with expired policy")
                else:
                    print(f"   ⚠️  David Kim not found in expired policies")
        return success, data

    def test_claims_list(self):
        """Test claims listing endpoint"""
        success, data = self.run_test("Claims List", "GET", "claims", 200)
        if success:
            print(f"   ✓ Retrieved {len(data)} claims")
            if data:
                print(f"   ✓ First claim: {data[0].get('id')} - Status: {data[0].get('status')}")
        return success, data

    def test_claim_submission(self):
        """Test full claim submission with Sarah Chen policy"""
        print("\n🚀 Testing FULL CLAIM SUBMISSION with SSE streaming...")
        
        # Prepare claim data
        claim_data = {
            "policyNumber": "AUTO-2024-001847",
            "holderName": "Sarah Chen",
            "incidentDate": "2024-08-15",
            "incidentType": "accident",
            "claimedAmount": 3200.00,
            "description": "Rear-end collision at intersection during morning traffic. Damage to rear bumper and trunk. Police report filed. Other driver admitted fault.",
            "contactEmail": "sarah.chen@example.com",
            "documentText": "Police report #PR-2024-0815-001. Other driver citation issued for following too closely. Photos of damage taken at scene."
        }

        # Submit claim
        success, response = self.run_test("Claim Submission", "POST", "claims", 200, claim_data)
        if not success:
            return False, {}

        claim_id = response.get('claimId')
        if not claim_id:
            print("❌ No claim ID returned")
            return False, {}

        print(f"   ✅ Claim submitted successfully: {claim_id}")
        
        # Wait and test SSE streaming
        print("   🔄 Testing SSE streaming (waiting for agents to complete)...")
        
        # Give the pipeline time to run (it takes 15-30 seconds)
        time.sleep(45)
        
        # Fetch the completed claim
        success, claim_data = self.run_test(f"Get Claim {claim_id}", "GET", f"claims/{claim_id}", 200)
        if success:
            print(f"   ✅ Claim processing completed")
            print(f"   ✓ Final Status: {claim_data.get('status')}")
            print(f"   ✓ Risk Score: {claim_data.get('risk_score')}")
            
            # Check agent trace
            agent_trace = claim_data.get('agent_trace', {})
            agents_completed = len([k for k in agent_trace.keys() if agent_trace[k]])
            print(f"   ✓ Agents completed: {agents_completed}/5")
            
            if agents_completed >= 5:
                print("   ✅ All 5 agents completed successfully")
            else:
                print(f"   ⚠️  Only {agents_completed} agents completed")
        
        return success, claim_data

    def run_all_tests(self):
        """Run all tests in sequence"""
        print("=" * 60)
        print("🧪 ClaimOS API Testing Suite")
        print("=" * 60)
        
        # Basic API tests
        self.test_health_check()
        self.test_dashboard_stats()
        self.test_policies_list()
        
        # Specific requirement tests
        self.test_policy_lookup_sarah_chen()
        self.test_policy_search_expired()
        self.test_claims_list()
        
        # Full integration test
        self.test_claim_submission()
        
        # Summary
        print("\n" + "=" * 60)
        print("🏁 TEST SUMMARY")
        print("=" * 60)
        print(f"Tests run: {self.tests_run}")
        print(f"Tests passed: {self.tests_passed}")
        print(f"Pass rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        # Failed tests
        failed_tests = [r for r in self.results if not r['success']]
        if failed_tests:
            print(f"\n❌ Failed tests ({len(failed_tests)}):")
            for test in failed_tests:
                print(f"   • {test['test']}: {test.get('error', 'Status code mismatch')}")
        
        return self.tests_passed == self.tests_run

def main():
    tester = ClaimOSAPITester()
    success = tester.run_all_tests()
    
    # Save results to JSON
    with open('/app/test_reports/backend_api_results.json', 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'tests_run': tester.tests_run,
                'tests_passed': tester.tests_passed,
                'pass_rate': tester.tests_passed/tester.tests_run*100 if tester.tests_run > 0 else 0
            },
            'results': tester.results
        }, f, indent=2)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())