import requests
import json
import base64
import time
import sys
import re

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class EnhancedMaximoClient:
    """
    Enhanced Maximo client that ensures updates are committed and verified
    """
    
    def __init__(self, host, user=None, password=None, api_key=None):
        """Initialize the client with auth details"""
        self.host = host.rstrip('/')
        self.user = user
        self.password = password
        self.api_key = api_key
        
        print(f"\n🔍 Connecting to Maximo at {host}...")
        
        # Setup API base URL
        self.base_url = f"{self.host}/maximo"
        self.api_url = f"{self.base_url}/api/os"
        self.oslc_url = f"{self.base_url}/oslc/os"
        self.rest_url = f"{self.base_url}/rest"
        
        # Setup authentication headers
        if api_key:
            self.auth_header = {"apikey": self.api_key}
        elif user and password:
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.basic_auth = {"Authorization": f"Basic {credentials}"}
            self.maxauth = {"maxauth": credentials}
            # Default to basic auth
            self.auth_header = self.basic_auth
        else:
            raise ValueError("Either API key or username/password must be provided")
        
        # Set up headers for different operations
        self.json_headers = {
            **self.auth_header, 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        print(f"✅ Client initialized for {host}")
        print(f"🔑 Auth method: {'API Key' if api_key else 'Basic Auth'}")

    def get_asset(self, assetnum, siteid, refresh=False):
        """
        Get asset details with refresh option to bypass cache
        """
        print(f"\n🔍 Looking up asset {assetnum} at site {siteid}...")
        
        # Add cache-busting parameter if refresh is requested
        cache_param = {"_lid": int(time.time())} if refresh else {}
        
        # Try OSLC API first (most reliable for data accuracy)
        try:
            oslc_params = {
                "oslc.where": f'spi:assetnum="{assetnum}" and spi:siteid="{siteid}"',
                "oslc.select": "*",
                **cache_param
            }
            
            print(f"  Querying via OSLC API...")
            response = requests.get(
                f"{self.oslc_url}/mxasset",
                headers=self.json_headers,
                params=oslc_params,
                verify=False,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse members based on response format
                members = None
                if "member" in data:
                    members = data["member"]
                elif "rdfs:member" in data:
                    members = data["rdfs:member"]
                
                if members and len(members) > 0:
                    asset = members[0]
                    print(f"✅ Asset found via OSLC API")
                    print(f"  Current status: {asset.get('spi:status', 'N/A')}")
                    print(f"  Current description: {asset.get('spi:description', 'N/A')}")
                    return asset
                
            print(f"  OSLC API failed or returned no results")
        except Exception as e:
            print(f"  Error with OSLC API: {str(e)}")
        
        # Try regular REST API as fallback
        try:
            rest_params = {
                "oslc.where": f'assetnum="{assetnum}" and siteid="{siteid}"',
                "oslc.select": "*",
                **cache_param
            }
            
            print(f"  Querying via REST API...")
            response = requests.get(
                f"{self.api_url}/mxasset",
                headers=self.json_headers,
                params=rest_params,
                verify=False,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse based on response format
                if "member" in data and len(data["member"]) > 0:
                    asset = data["member"][0]
                    print(f"✅ Asset found via REST API")
                    print(f"  Current status: {asset.get('status', 'N/A')}")
                    print(f"  Current description: {asset.get('description', 'N/A')}")
                    return asset
                
            print(f"  REST API failed or returned no results")
        except Exception as e:
            print(f"  Error with REST API: {str(e)}")
        
        # If we get here, both methods failed
        error_msg = f"Could not find asset {assetnum} at site {siteid}"
        print(f"❌ {error_msg}")
        raise Exception(error_msg)
    
    def update_asset(self, assetnum, siteid, update_data, verify=True):
        """
        Update asset with verification to ensure changes persist
        
        Args:
            assetnum (str): Asset number
            siteid (str): Site ID
            update_data (dict): Fields to update
            verify (bool): Whether to verify the update after completion
            
        Returns:
            dict: Result information
        """
        print(f"\n🔄 Updating asset {assetnum} at site {siteid}...")
        
        # Parse update data if it's a string
        if isinstance(update_data, str):
            update_fields = json.loads(update_data)
        else:
            update_fields = update_data
            
        print(f"  Fields to update: {json.dumps(update_fields)}")
        
        # First get the current asset to check if it exists
        try:
            asset = self.get_asset(assetnum, siteid)
        except Exception as e:
            print(f"❌ Cannot update - asset lookup failed: {str(e)}")
            raise
        
        # Get the href (resource URI) for direct updates
        if "href" in asset:
            resource_uri = asset["href"]
        elif "rdf:about" in asset:
            resource_uri = asset["rdf:about"]
        else:
            # Construct URI based on pattern
            resource_uri = f"{self.oslc_url}/mxasset"
            print(f"⚠️ Warning: Could not find direct resource URI, using collection endpoint")
        
        print(f"  Resource URI: {resource_uri}")
        
        # Get _rowstamp if available for optimistic locking
        rowstamp = None
        if "rdf:about" in asset and "spi:_rowstamp" in asset:
            rowstamp = asset.get("spi:_rowstamp")
        elif "_rowstamp" in asset:
            rowstamp = asset.get("_rowstamp")
        
        # Try direct OSLC PATCH first (most reliable)
        success = False
        try:
            # Prepare OSLC payload with proper namespace prefixes
            oslc_payload = {}
            
            # Add core identifying fields without prefix
            if "where" not in resource_uri:
                # Add identifying fields only for collection endpoint
                oslc_payload["spi:assetnum"] = assetnum
                oslc_payload["spi:siteid"] = siteid
            
            # Add rowstamp if available for concurrency control
            if rowstamp:
                oslc_payload["spi:_rowstamp"] = rowstamp
            
            # Add update fields with spi: namespace
            for key, value in update_fields.items():
                if key.startswith("spi:"):
                    oslc_payload[key] = value
                else:
                    oslc_payload[f"spi:{key}"] = value
            
            # Add properties header to specify which fields are being updated
            properties = ",".join(k.replace("spi:", "") for k in oslc_payload 
                                 if not k.startswith("spi:_") and k != "spi:assetnum" and k != "spi:siteid")
            
            # Special headers for PATCH operation
            patch_headers = {
                **self.json_headers,
                "x-method-override": "PATCH",
                "Properties": properties
            }
            
            print(f"  Sending OSLC PATCH request...")
            print(f"  Properties: {properties}")
            print(f"  Payload: {json.dumps(oslc_payload)}")
            
            # If we have a collection URI, add where clause
            if "where" not in resource_uri:
                params = {}
                if "href" not in asset and "rdf:about" not in asset:
                    params["oslc.where"] = f'spi:assetnum="{assetnum}" and spi:siteid="{siteid}"'
            else:
                params = None
            
            # Send the update request
            response = requests.post(
                resource_uri,
                headers=patch_headers,
                params=params,
                json=oslc_payload,
                verify=False,
                timeout=60
            )
            
            # Check if update was successful
            if response.status_code in [200, 201, 204]:
                print(f"✅ OSLC PATCH request successful: Status {response.status_code}")
                success = True
            else:
                print(f"❌ OSLC PATCH request failed: Status {response.status_code}")
                if response.text:
                    print(f"  Response: {response.text[:500]}")
                print("  Trying alternative method...")
        except Exception as e:
            print(f"❌ Error with OSLC PATCH: {str(e)}")
            print("  Trying alternative method...")
        
        # If OSLC PATCH failed, try the REST API with _action=Change
        if not success:
            try:
                # Prepare REST API payload
                rest_payload = {
                    "ASSET": [{
                        "ASSETNUM": assetnum,
                        "SITEID": siteid
                    }]
                }
                
                # Add update fields with uppercase for REST API
                for key, value in update_fields.items():
                    key_upper = key.upper()
                    rest_payload["ASSET"][0][key_upper] = value
                
                # Add explicit action parameters
                params = {
                    "_action": "Change",
                    "oslc.where": f'assetnum="{assetnum}" and siteid="{siteid}"'
                }
                
                print(f"  Sending REST API request with _action=Change...")
                print(f"  Payload: {json.dumps(rest_payload)}")
                
                response = requests.post(
                    f"{self.api_url}/mxasset",
                    headers=self.json_headers,
                    params=params,
                    json=rest_payload,
                    verify=False,
                    timeout=60
                )
                
                if response.status_code in [200, 201, 204]:
                    print(f"✅ REST API request successful: Status {response.status_code}")
                    success = True
                else:
                    print(f"❌ REST API request failed: Status {response.status_code}")
                    if response.text:
                        print(f"  Response: {response.text[:500]}")
                    print("  Both update methods failed")
            except Exception as e:
                print(f"❌ Error with REST API: {str(e)}")
                print("  Both update methods failed")
        
        # If both methods failed, raise exception
        if not success:
            raise Exception(f"Failed to update asset {assetnum} at site {siteid} using any method")
        
        # Verify update if requested
        if verify:
            print("\n🔍 Verifying update...")
            time.sleep(2)  # Give Maximo time to process
            
            # Get asset with cache refresh
            try:
                updated_asset = self.get_asset(assetnum, siteid, refresh=True)
                
                # Check if updates are reflected
                verification_passed = True
                for key, expected_value in update_fields.items():
                    # Check both prefixed and non-prefixed fields
                    actual_value = updated_asset.get(f"spi:{key}", updated_asset.get(key))
                    
                    if actual_value != expected_value:
                        print(f"❌ Verification failed for field '{key}'")
                        print(f"  Expected: {expected_value}")
                        print(f"  Actual: {actual_value}")
                        verification_passed = False
                
                if verification_passed:
                    print("✅ Update verified - all changes are reflected in Maximo")
                else:
                    print("⚠️ Warning: Some changes were not reflected in Maximo")
                    print("  This might indicate validation issues or workflow restrictions")
                    
                return {
                    "success": True,
                    "verified": verification_passed,
                    "message": "Asset updated successfully" if verification_passed else "Asset updated but some changes not verified"
                }
                
            except Exception as e:
                print(f"⚠️ Warning: Could not verify update: {str(e)}")
                return {
                    "success": True,
                    "verified": False,
                    "message": "Asset updated but verification failed"
                }
        
        return {
            "success": True,
            "verified": False,
            "message": "Asset updated successfully (not verified)"
        }
                
    def update_asset_status(self, assetnum, siteid, new_status):
        """Update an asset's status with verification"""
        return self.update_asset(assetnum, siteid, {"status": new_status})
    
    def update_asset_description(self, assetnum, siteid, new_description):
        """Update an asset's description with verification"""
        return self.update_asset(assetnum, siteid, {"description": new_description})


# Example usage
if __name__ == "__main__":
    # CONFIGURATION
    HOST = "http://mx7vm"
    API_KEY = "pk4r5qvq"
    USERNAME = "wilson"
    PASSWORD = "wilson"
    ASSET_NUM = "13150"
    SITE_ID = "BEDFORD"
    
    print("\n" + "=" * 70)
    print("ENHANCED MAXIMO CLIENT TEST")
    print("=" * 70)
    
    try:
        # First try with API key
        print("\n🔑 Testing with API key authentication...")
        client = EnhancedMaximoClient(host=HOST, api_key=API_KEY)
        
        # Try updating asset description
        test_description = f"Updated via Enhanced Client at {time.strftime('%H:%M:%S')}"
        result = client.update_asset_description(ASSET_NUM, SITE_ID, test_description)
        print(f"✅ Description update result: {result}")
        
        # Try updating asset status
        # Choose a valid status value from your Maximo installation
        valid_status = "OPERATING"  # Or "ACTIVE", "DECOMMISSIONED" etc.
        result = client.update_asset_status(ASSET_NUM, SITE_ID, valid_status)
        print(f"✅ Status update result: {result}")
        
    except Exception as e:
        print(f"❌ API key authentication failed: {str(e)}")
        
        # If API key fails, try with basic authentication
        try:
            print("\n🔑 Testing with basic authentication...")
            client = EnhancedMaximoClient(
                host=HOST,
                user=USERNAME,
                password=PASSWORD
            )
            
            # Try updating asset description
            test_description = f"Updated via Enhanced Client with Basic Auth at {time.strftime('%H:%M:%S')}"
            result = client.update_asset_description(ASSET_NUM, SITE_ID, test_description)
            print(f"✅ Description update result: {result}")
            
            # Try updating asset status
            result = client.update_asset_status(ASSET_NUM, SITE_ID, valid_status)
            print(f"✅ Status update result: {result}")
            
        except Exception as e:
            print(f"❌ Basic authentication also failed: {str(e)}")
            
    print("\n" + "=" * 70)



######################
