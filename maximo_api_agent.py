import os
import requests
import json
import argparse
import urllib3
import time
import base64
from urllib.parse import urlencode

# Suppress only the single InsecureRequestWarning from urllib3 needed for self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
MAXIMO_HOST = os.environ.get("MAXIMO_HOST", "YOUR_MAXIMO_HOST_HERE")
API_KEY = os.environ.get("MAXIMO_API_KEY", "YOUR_MAXIMO_API_KEY_HERE")

class MaximoAPIClient:
    """
    A client for interacting with the IBM Maximo API that works across different versions.
    Implements multiple approaches for maximum compatibility.
    """
    def __init__(self, host, api_key=None, user=None, password=None):
        if not host or "your.maximo.com" in host:
            raise ValueError(f"MAXIMO_HOST is not configured correctly. The value received was '{host}'. Please set it as an environment variable or hardcode it in the script.")
        
        if api_key:
            if not api_key or "your_long_api_key" in api_key or "apikey" == api_key:
                raise ValueError(f"API_KEY is not configured correctly. Please set it as an environment variable or hardcode it in the script.")
        elif not (user and password):
            raise ValueError("Either API key or username/password must be provided")
        
        self.host = host.rstrip('/')
        self.api_key = api_key
        self.user = user
        self.password = password
        
        # Set up base URLs for different API patterns
        self.base_url = f"{self.host}/maximo"
        self.api_url = f"{self.base_url}/api/os"
        self.oslc_url = f"{self.base_url}/oslc/os"
        self.rest_url = f"{self.base_url}/rest"
        
        # Set up authentication headers
        if api_key:
            self.auth_header = {"apikey": self.api_key}
        elif user and password:
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            # Both forms of authentication for maximum compatibility
            self.basic_auth_header = {"Authorization": f"Basic {credentials}"}
            self.maxauth_header = {"maxauth": credentials}
            # Default to basic auth
            self.auth_header = self.basic_auth_header
            # Parameters for MBO REST API
            self.auth_params = {"_lid": user, "_lpwd": password}
        
        # Common headers for different operations
        self.headers = {
            **self.auth_header,
            "Accept": "application/json"
        }
        
        self.json_headers = {
            **self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        print(f"Initialized Maximo client for {host}")
        print(f"Authentication method: {'API Key' if api_key else 'Username/Password'}")

    def test_connection(self):
        """Test connection using mxperson"""
        url = f"{self.api_url}/mxperson"
        params = {
            "oslc.pageSize": 1,
            "oslc.select": "personid,displayname",
            "lean": 1,
            "_format": "json"
        }
        
        print(f"--> Performing test query against: {url}?{urlencode(params)}")
        
        try:
            response = requests.get(url, params=params, headers=self.headers, verify=False, timeout=10)
            if response.ok:
                return response.json()
            else:
                print(f"❌ API Error during connection test: {response.status_code} - {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ CRITICAL: A network error occurred during connection test.\n   Error: {e}")
            return None

    def get_asset(self, assetnum: str, siteid: str = None, fields_to_select: str = None) -> list | None:
        """
        Retrieves details for one or more assets using OSLC API for compatibility with spi: namespace.
        """
        print(f"\n🔍 Looking up asset {assetnum}" + (f" at site {siteid}" if siteid else ""))
        
        # First try the OSLC API with spi: prefixes - this gives the most complete data
        try:
            # Handle single or multiple asset numbers
            if "," in assetnum:
                asset_list = [f'"{a.strip()}"' for a in assetnum.split(',')]
                where_clause = f'spi:assetnum in [{",".join(asset_list)}]'
            else:
                where_clause = f'spi:assetnum="{assetnum.strip()}"'
            
            # Add site to where clause
            if siteid:
                where_clause += f' and spi:siteid="{siteid}"'
            
            # Default fields if none are provided
            if not fields_to_select:
                fields_to_select = "assetnum,description,status,assettype,calnum"
            
            # Ensure we have assetnum in the fields
            fields_list = fields_to_select.split(',')
            if "assetnum" not in [f.strip().lower() for f in fields_list]:
                fields_list.append("assetnum")
            
            # Always add a timestamp to prevent caching
            params = {
                "oslc.where": where_clause,
                "oslc.select": "*",  # Request all fields to ensure we get everything needed
                "_ts": int(time.time())
            }
            
            oslc_url = f"{self.oslc_url}/mxasset"
            print(f"  Trying OSLC API with spi: prefixes...")
            print(f"  URL: {oslc_url}")
            
            response = requests.get(
                oslc_url,
                headers=self.headers,
                params=params,
                verify=False,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Handle both member formats
                members = None
                if "member" in data:
                    members = data["member"]
                elif "rdfs:member" in data:
                    members = data["rdfs:member"]
                
                if members and len(members) > 0:
                    print(f"✅ Successfully retrieved {len(members)} assets via OSLC API")
                    
                    # Clean up the response by removing the spi: prefixes and 
                    # keeping only the requested fields
                    clean_assets = []
                    
                    for asset in members:
                        clean_asset = {}
                        
                        # Go through all requested fields
                        for field in fields_list:
                            field = field.strip()
                            
                            # Check for field with both prefixed and non-prefixed versions
                            # First try non-prefixed (in case it's already in that format)
                            if field in asset:
                                clean_asset[field] = asset[field]
                            # Then try with spi: prefix (most common in OSLC API)
                            elif f"spi:{field}" in asset:
                                clean_asset[field] = asset[f"spi:{field}"]
                            # Fields might be returned in different case
                            elif field.lower() in [k.lower() for k in asset.keys()]:
                                # Find the actual key with case insensitive match
                                for k in asset.keys():
                                    if k.lower() == field.lower():
                                        clean_asset[field] = asset[k]
                                        break
                            elif f"spi:{field}".lower() in [k.lower() for k in asset.keys()]:
                                # Find the actual key with spi: prefix and case insensitive match
                                for k in asset.keys():
                                    if k.lower() == f"spi:{field}".lower():
                                        clean_asset[field] = asset[k]
                                        break
                        
                        # Ensure assetnum is included
                        if "assetnum" not in clean_asset and "spi:assetnum" in asset:
                            clean_asset["assetnum"] = asset["spi:assetnum"]
                            
                        clean_assets.append(clean_asset)
                        
                    return clean_assets
        except Exception as e:
            print(f"  Error with OSLC API: {str(e)}")
        
        # If OSLC API failed, try the standard REST API
        try:
            # Handle single or multiple asset numbers
            if "," in assetnum:
                asset_list = [f'"{a.strip()}"' for a in assetnum.split(',')]
                where_clause = f'assetnum in [{",".join(asset_list)}]'
            else:
                where_clause = f'assetnum="{assetnum.strip()}"'
            
            # Add site to where clause
            if siteid:
                where_clause += f' and siteid="{siteid}"'
            
            # Default fields if none are provided
            if not fields_to_select:
                select_fields = "assetnum,description,status,assettype,calnum"
            else:
                select_fields = fields_to_select
            
            # Ensure assetnum is included
            fields_list = select_fields.split(',')
            if "assetnum" not in [f.strip().lower() for f in fields_list]:
                select_fields = "assetnum," + select_fields
                
            # Add a timestamp to prevent caching
            params = {
                "oslc.where": where_clause,
                "oslc.select": select_fields,
                "lean": 1,
                "_format": "json",
                "_ts": int(time.time())
            }
            
            rest_url = f"{self.api_url}/mxasset"
            print(f"  Trying REST API as fallback...")
            print(f"  URL: {rest_url}")
            
            response = requests.get(
                rest_url,
                headers=self.headers,
                params=params,
                verify=False,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if "member" in data and data.get("member"):
                    assets = data["member"]
                    print(f"✅ Successfully retrieved {len(assets)} assets via REST API")
                    return assets
        except Exception as e:
            print(f"  Error with REST API: {str(e)}")
        
        # If all methods failed, return empty list
        print(f"❌ Failed to retrieve asset data through any available method")
        return []

    def get_location(self, location: str, siteid: str = None, fields_to_select: str = None) -> list | None:
        """
        Retrieves details for one or more locations using OSLC API for compatibility with spi: namespace.
        """
        print(f"\n🔍 Looking up location {location}" + (f" at site {siteid}" if siteid else ""))
        
        # First try the OSLC API with spi: prefixes
        try:
            # Handle single or multiple location IDs
            if "," in location:
                location_list = [f'"{l.strip()}"' for l in location.split(',')]
                where_clause = f'spi:location in [{",".join(location_list)}]'
            else:
                where_clause = f'spi:location="{location.strip()}"'
            
            # Add site to where clause
            if siteid:
                where_clause += f' and spi:siteid="{siteid}"'
            
            # Default fields if none are provided
            if not fields_to_select:
                fields_to_select = "location,description,status"
            
            # Ensure we have location in the fields
            fields_list = fields_to_select.split(',')
            if "location" not in [f.strip().lower() for f in fields_list]:
                fields_list.append("location")
            
            # Always add a timestamp to prevent caching
            params = {
                "oslc.where": where_clause,
                "oslc.select": "*",  # Request all fields to ensure we get everything needed
                "_ts": int(time.time())
            }
            
            oslc_url = f"{self.oslc_url}/mxlocation"
            print(f"  Trying OSLC API with spi: prefixes...")
            print(f"  URL: {oslc_url}")
            
            response = requests.get(
                oslc_url,
                headers=self.headers,
                params=params,
                verify=False,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Handle both member formats
                members = None
                if "member" in data:
                    members = data["member"]
                elif "rdfs:member" in data:
                    members = data["rdfs:member"]
                
                if members and len(members) > 0:
                    print(f"✅ Successfully retrieved {len(members)} locations via OSLC API")
                    
                    # Clean up the response by removing the spi: prefixes and 
                    # keeping only the requested fields
                    clean_locations = []
                    
                    for loc in members:
                        clean_loc = {}
                        
                        # Go through all requested fields
                        for field in fields_list:
                            field = field.strip()
                            
                            # Check for field with both prefixed and non-prefixed versions
                            if field in loc:
                                clean_loc[field] = loc[field]
                            elif f"spi:{field}" in loc:
                                clean_loc[field] = loc[f"spi:{field}"]
                            # Fields might be returned in different case
                            elif field.lower() in [k.lower() for k in loc.keys()]:
                                for k in loc.keys():
                                    if k.lower() == field.lower():
                                        clean_loc[field] = loc[k]
                                        break
                            elif f"spi:{field}".lower() in [k.lower() for k in loc.keys()]:
                                for k in loc.keys():
                                    if k.lower() == f"spi:{field}".lower():
                                        clean_loc[field] = loc[k]
                                        break
                        
                        # Ensure location is included
                        if "location" not in clean_loc and "spi:location" in loc:
                            clean_loc["location"] = loc["spi:location"]
                            
                        clean_locations.append(clean_loc)
                        
                    return clean_locations
        except Exception as e:
            print(f"  Error with OSLC API: {str(e)}")
        
        # If OSLC API failed, try the standard REST API
        try:
            # Handle single or multiple locations
            if "," in location:
                location_list = [f'"{l.strip()}"' for l in location.split(',')]
                where_clause = f'location in [{",".join(location_list)}]'
            else:
                where_clause = f'location="{location.strip()}"'
            
            if siteid:
                where_clause += f' and siteid="{siteid}"'
            
            # Default fields if none are provided
            if not fields_to_select:
                select_fields = "location,description,status"
            else:
                select_fields = fields_to_select
            
            # Ensure location is included
            fields_list = select_fields.split(',')
            if "location" not in [f.strip().lower() for f in fields_list]:
                select_fields = "location," + select_fields
                
            # Add a timestamp to prevent caching
            params = {
                "oslc.where": where_clause,
                "oslc.select": select_fields,
                "lean": 1,
                "_format": "json",
                "_ts": int(time.time())
            }
            
            rest_url = f"{self.api_url}/mxlocation"
            print(f"  Trying REST API as fallback...")
            print(f"  URL: {rest_url}")
            
            response = requests.get(
                rest_url,
                headers=self.headers,
                params=params,
                verify=False,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if "member" in data and data.get("member"):
                    locations = data["member"]
                    print(f"✅ Successfully retrieved {len(locations)} locations via REST API")
                    return locations
        except Exception as e:
            print(f"  Error with REST API: {str(e)}")
        
        # If all methods failed, return empty list
        print(f"❌ Failed to retrieve location data through any available method")
        return []

    def update_asset(self, assetnum, fields_to_update, siteid=None):
        """
        Updates one or more fields of an asset using multiple methods for compatibility.
        Properly handles spi: namespace prefixes.
        
        Args:
            assetnum (str): Asset number to update
            fields_to_update (str or dict): JSON string or dictionary of fields to update
            siteid (str, optional): Site ID for the asset
            
        Returns:
            dict: Result information
        """
        print(f"\n🔄 Updating asset {assetnum}" + (f" at site {siteid}" if siteid else ""))
        
        # Parse fields_to_update if it's a string
        if isinstance(fields_to_update, str):
            try:
                update_data = json.loads(fields_to_update)
            except json.JSONDecodeError:
                print(f"❌ Invalid JSON in fields_to_update: {fields_to_update}")
                return None
        else:
            update_data = fields_to_update
            
        print(f"  Fields to update: {json.dumps(update_data)}")
        
        # First get the current asset to check if it exists and get resource URI
        try:
            assets = self.get_asset(assetnum, siteid)
            if not assets:
                print(f"❌ Cannot update - asset not found")
                return None
                
            # Get the asset's URI for direct updates if possible
            asset_href = self._get_record_href("mxasset", f'assetnum="{assetnum}"' + (f' and siteid="{siteid}"' if siteid else ''))
            if not asset_href:
                print("⚠️ Could not get direct resource URI, will use collection endpoint")
        except Exception as e:
            print(f"❌ Cannot update - asset lookup failed: {str(e)}")
            return None
        
        # Try the OSLC PATCH approach first (most reliable)
        success = False
        try:
            # Prepare OSLC payload with proper namespace prefixes
            oslc_payload = {}
            
            # Include identifiers if we don't have direct URI
            if not asset_href:
                oslc_payload["spi:assetnum"] = assetnum
                if siteid:
                    oslc_payload["spi:siteid"] = siteid
            
            # Add update fields with spi: namespace
            for key, value in update_data.items():
                if key.startswith("spi:"):
                    oslc_payload[key] = value
                else:
                    oslc_payload[f"spi:{key}"] = value
            
            # Properties header for field list
            properties = ",".join(k.replace("spi:", "") for k in oslc_payload 
                               if not k.startswith("spi:_") and k != "spi:assetnum" and k != "spi:siteid")
            
            # Special headers for PATCH
            patch_headers = {
                **self.json_headers,
                "x-method-override": "PATCH",
                "Properties": properties
            }
            
            # Use direct URI if available, otherwise collection endpoint
            oslc_url = asset_href if asset_href else f"{self.oslc_url}/mxasset"
            
            # Parameters for collection endpoint if needed
            params = {}
            if not asset_href:
                where_clause = f'spi:assetnum="{assetnum}"'
                if siteid:
                    where_clause += f' and spi:siteid="{siteid}"'
                params["oslc.where"] = where_clause
            
            print(f"  Sending OSLC PATCH request...")
            print(f"  URL: {oslc_url}")
            print(f"  Properties: {properties}")
            print(f"  Payload: {json.dumps(oslc_payload)}")
            
            # Send the request
            response = requests.post(
                oslc_url,
                headers=patch_headers,
                params=params,
                json=oslc_payload,
                verify=False,
                timeout=60
            )
            
            # Check response
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
                        "ASSETNUM": assetnum
                    }]
                }
                
                # Add siteid if provided
                if siteid:
                    rest_payload["ASSET"][0]["SITEID"] = siteid
                
                # Add update fields with uppercase
                for key, value in update_data.items():
                    rest_payload["ASSET"][0][key.upper()] = value
                
                # Action parameters
                params = {
                    "_action": "Change",
                    "oslc.where": f'assetnum="{assetnum}"' + (f' and siteid="{siteid}"' if siteid else '')
                }
                
                print(f"  Sending REST API request with _action=Change...")
                print(f"  URL: {self.api_url}/mxasset")
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
        
        # If both methods failed, return failure
        if not success:
            return None
        
        # Verify the update if successful
        print("\n🔍 Verifying update...")
        time.sleep(2)  # Give Maximo time to process
        
        try:
            # Get fresh asset data with fields we updated
            # Determine which fields to request
            fields_to_request = ["assetnum"]
            for field in update_data.keys():
                if field not in fields_to_request:
                    fields_to_request.append(field)
                    
            fields_str = ",".join(fields_to_request)
            
            updated_assets = self.get_asset(assetnum, siteid, fields_to_select=fields_str)
            if not updated_assets:
                print("⚠️ Could not verify update - asset not found after update")
                return {"status": "success", "message": f"Asset {assetnum} update accepted but could not verify changes"}
                
            updated_asset = updated_assets[0]
            
            # Check if all fields were updated correctly
            verification_results = {}
            all_verified = True
            
            for field, expected_value in update_data.items():
                # Try to find the field - it might be with or without prefix
                actual_value = None
                
                # Check for field with and without spi: prefix
                if field in updated_asset:
                    actual_value = updated_asset[field]
                elif f"spi:{field}" in updated_asset:
                    actual_value = updated_asset[f"spi:{field}"]
                    
                # Check case insensitive
                elif field.lower() in [k.lower() for k in updated_asset.keys()]:
                    for k in updated_asset.keys():
                        if k.lower() == field.lower():
                            actual_value = updated_asset[k]
                            break
                            
                # Check with spi: prefix case insensitive
                elif f"spi:{field}".lower() in [k.lower() for k in updated_asset.keys()]:
                    for k in updated_asset.keys():
                        if k.lower() == f"spi:{field}".lower():
                            actual_value = updated_asset[k]
                            break
                
                if actual_value == expected_value:
                    verification_results[field] = {"verified": True, "value": actual_value}
                else:
                    verification_results[field] = {
                        "verified": False,
                        "expected": expected_value,
                        "actual": actual_value
                    }
                    all_verified = False
            
            if all_verified:
                return {
                    "status": "success",
                    "message": f"Asset {assetnum} successfully updated and all changes verified.",
                    "verification": verification_results
                }
            else:
                print("⚠️ Warning: Some fields did not update as expected.")
                print("  This might indicate validation issues or workflow restrictions.")
                return {
                    "status": "partial_success",
                    "message": f"Asset {assetnum} update was accepted but some changes were not applied.",
                    "verification": verification_results
                }
                
        except Exception as e:
            print(f"⚠️ Warning: Could not verify update: {str(e)}")
            return {
                "status": "success",
                "message": f"Asset {assetnum} update accepted but verification failed",
                "error": str(e)
            }

    def update_location(self, location, fields_to_update, siteid=None):
        """
        Updates one or more fields of a location using multiple methods for compatibility.
        Properly handles spi: namespace prefixes.
        
        Args:
            location (str): Location ID to update
            fields_to_update (str or dict): JSON string or dictionary of fields to update
            siteid (str, optional): Site ID for the location
            
        Returns:
            dict: Result information
        """
        print(f"\n🔄 Updating location {location}" + (f" at site {siteid}" if siteid else ""))
        
        # Parse fields_to_update if it's a string
        if isinstance(fields_to_update, str):
            try:
                update_data = json.loads(fields_to_update)
            except json.JSONDecodeError:
                print(f"❌ Invalid JSON in fields_to_update: {fields_to_update}")
                return None
        else:
            update_data = fields_to_update
            
        print(f"  Fields to update: {json.dumps(update_data)}")
        
        # First get the current location to check if it exists
        try:
            locations = self.get_location(location, siteid)
            if not locations:
                print(f"❌ Cannot update - location not found")
                return None
                
            # Get the location's URI for direct updates
            location_href = self._get_record_href("mxlocation", f'location="{location}"' + (f' and siteid="{siteid}"' if siteid else ''))
            if not location_href:
                print("⚠️ Could not get direct resource URI, will use collection endpoint")
        except Exception as e:
            print(f"❌ Cannot update - location lookup failed: {str(e)}")
            return None
        
        # Try the OSLC PATCH approach first (most reliable)
        success = False
        try:
            # Prepare OSLC payload with proper namespace prefixes
            oslc_payload = {}
            
            # Include identifiers if we don't have direct URI
            if not location_href:
                oslc_payload["spi:location"] = location
                if siteid:
                    oslc_payload["spi:siteid"] = siteid
            
            # Add update fields with spi: namespace
            for key, value in update_data.items():
                if key.startswith("spi:"):
                    oslc_payload[key] = value
                else:
                    oslc_payload[f"spi:{key}"] = value
            
            # Properties header for field list
            properties = ",".join(k.replace("spi:", "") for k in oslc_payload 
                               if not k.startswith("spi:_") and k != "spi:location" and k != "spi:siteid")
            
            # Special headers for PATCH
            patch_headers = {
                **self.json_headers,
                "x-method-override": "PATCH",
                "Properties": properties
            }
            
            # Use direct URI if available, otherwise collection endpoint
            oslc_url = location_href if location_href else f"{self.oslc_url}/mxlocation"
            
            # Parameters for collection endpoint if needed
            params = {}
            if not location_href:
                where_clause = f'spi:location="{location}"'
                if siteid:
                    where_clause += f' and spi:siteid="{siteid}"'
                params["oslc.where"] = where_clause
            
            print(f"  Sending OSLC PATCH request...")
            print(f"  URL: {oslc_url}")
            print(f"  Properties: {properties}")
            print(f"  Payload: {json.dumps(oslc_payload)}")
            
            # Send the request
            response = requests.post(
                oslc_url,
                headers=patch_headers,
                params=params,
                json=oslc_payload,
                verify=False,
                timeout=60
            )
            
            # Check response
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
                    "LOCATIONS": [{
                        "LOCATION": location
                    }]
                }
                
                # Add siteid if provided
                if siteid:
                    rest_payload["LOCATIONS"][0]["SITEID"] = siteid
                
                # Add update fields with uppercase
                for key, value in update_data.items():
                    rest_payload["LOCATIONS"][0][key.upper()] = value
                
                # Action parameters
                params = {
                    "_action": "Change",
                    "oslc.where": f'location="{location}"' + (f' and siteid="{siteid}"' if siteid else '')
                }
                
                print(f"  Sending REST API request with _action=Change...")
                print(f"  URL: {self.api_url}/mxlocation")
                print(f"  Payload: {json.dumps(rest_payload)}")
                
                response = requests.post(
                    f"{self.api_url}/mxlocation",
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
        
        # If both methods failed, return failure
        if not success:
            return None
        
        # For simplicity, report success without detailed verification
        return {
            "status": "success",
            "message": f"Location {location} update accepted"
        }

    def update_asset_status(self, assetnum, new_status, siteid=None):
        """
        Updates just the status of an asset. Convenience method that calls update_asset.
        """
        return self.update_asset(assetnum, {"status": new_status}, siteid)
    
    def create_asset(self, siteid, asset_data):
        """
    Creates a new asset in Maximo with auto-generated asset number.
    Properly handles spi: namespace prefixes and uses multiple methods for compatibility.
    
    Args:
        siteid (str): The site ID for the asset (required)
        asset_data (str or dict): JSON string or dictionary with asset data
        
    Returns:
        dict: The created asset data with auto-generated asset number
    """
    print(f"\n➕ Creating new asset at site {siteid}")
    
    # Check if siteid is provided (required)
    if not siteid:
        print("❌ Site ID is required for asset creation")
        return None
    
    # Parse asset_data if it's a string
    if isinstance(asset_data, str):
        try:
            create_fields = json.loads(asset_data)
        except json.JSONDecodeError:
            print(f"❌ Invalid JSON in asset_data: {asset_data}")
            return None
    else:
        create_fields = asset_data
        
    print(f"  Asset data: {json.dumps(create_fields)}")
    
    # Try OSLC API first (most reliable for creation)
    success = False
    created_assetnum = None
    try:
        # Prepare OSLC payload with proper namespace prefixes
        oslc_payload = {"spi:siteid": siteid}
        
        # Add asset fields with spi: namespace
        for key, value in create_fields.items():
            if key.lower() != "siteid":  # Skip siteid as we already added it
                if key.startswith("spi:"):
                    oslc_payload[key] = value
                else:
                    oslc_payload[f"spi:{key}"] = value
        
        # Properties header - all fields except internal ones
        properties = "siteid," + ",".join(
            k.replace("spi:", "") for k in oslc_payload
            if not k.startswith("spi:_") and k != "spi:siteid" and k != "spi:assetnum"
        )
        
        # Headers for creation
        create_headers = {
            **self.json_headers,
            "Properties": properties
        }
        
        print(f"  Sending OSLC creation request...")
        print(f"  Properties: {properties}")
        print(f"  Payload: {json.dumps(oslc_payload)}")
        
        # OSLC creation endpoint
        oslc_url = f"{self.oslc_url}/mxasset"
        
        # Send the creation request
        response = requests.post(
            oslc_url,
            headers=create_headers,
            json=oslc_payload,
            verify=False,
            timeout=60
        )
        
        # Check response
        if response.status_code in [200, 201]:
            print(f"✅ OSLC creation successful: Status {response.status_code}")
            
            # Parse response to get created asset
            created_asset = response.json()
            
            # Extract asset number from various possible response formats
            if "spi:assetnum" in created_asset:
                created_assetnum = created_asset["spi:assetnum"]
            elif "assetnum" in created_asset:
                created_assetnum = created_asset["assetnum"]
            elif "rdf:about" in created_asset:
                # Try to extract from resource URI
                uri = created_asset["rdf:about"]
                try:
                    # Different patterns based on Maximo version
                    if "_" in uri and "/" in uri:
                        # Pattern like "/mxasset/_ABCDE"
                        parts = uri.split("_")
                        if len(parts) > 1:
                            encoded_part = parts[-1].split("/")[0]
                            # Sometimes the assetnum is base64 encoded
                            try:
                                import base64
                                decoded = base64.b64decode(encoded_part + "==").decode('utf-8')
                                if "/" in decoded:
                                    created_assetnum = decoded.split("/")[0]
                                else:
                                    created_assetnum = decoded
                            except:
                                created_assetnum = encoded_part
                    elif "assetnum=" in uri:
                        # Pattern with query parameter: "assetnum=12345"
                        params = uri.split("?")[-1].split("&")
                        for param in params:
                            if param.startswith("assetnum="):
                                created_assetnum = param.split("=")[1].strip('"')
                                break
                except Exception as e:
                    print(f"  Error extracting assetnum from URI: {str(e)}")
            
            if created_assetnum:
                print(f"✅ Asset created with number: {created_assetnum}")
                success = True
            else:
                print("⚠️ Asset appears to be created but couldn't determine asset number")
                print(f"  Response: {json.dumps(created_asset)[:500]}")
                # Still mark as success but we'll need to extract the asset number later
                success = True
        else:
            print(f"❌ OSLC creation failed: Status {response.status_code}")
            if response.text:
                print(f"  Response: {response.text[:500]}")
            print("  Trying alternative method...")
    except Exception as e:
        print(f"❌ Error with OSLC creation: {str(e)}")
        print("  Trying alternative method...")
    
    # If OSLC creation failed, try REST API
    def create_asset(self, siteid, asset_data):
        """
    Create a new asset in Maximo with automatic asset number generation.
    
    Args:
        siteid (str): The site ID (mandatory for asset creation)
        asset_data (dict or str): Data for the new asset - description, location, etc.
        
    Returns:
        dict: The newly created asset data including the generated asset number
    """
    print(f"\n➕ Creating new asset in site {siteid}...")
    
    # Parse asset_data if it's a string
    if isinstance(asset_data, str):
        try:
            asset_fields = json.loads(asset_data)
        except json.JSONDecodeError:
            print(f"❌ Invalid JSON in asset_data: {asset_data}")
            return None
    else:
        asset_fields = asset_data
        
    print(f"  Asset fields: {json.dumps(asset_fields)}")
    
    # Validate siteid is provided (required)
    if not siteid:
        print("❌ Site ID is required for asset creation")
        return None
        
    # Try different create methods
    create_methods = [
        self._create_asset_oslc,
        self._create_asset_rest,
        self._create_asset_object_structure
    ]
    
    for method in create_methods:
        try:
            result = method(siteid, asset_fields)
            if result:
                # Get the newly created asset with all its details
                assetnum = result.get('assetnum')
                if assetnum:
                    print(f"✅ Successfully created asset {assetnum}")
                    # Get complete asset details
                    complete_asset = self.get_asset(assetnum, siteid)
                    if complete_asset:
                        return complete_asset
                    else:
                        # Return what we have if we can't get complete details
                        return result
                else:
                    return result
        except Exception as e:
            print(f"  Create method {method.__name__} failed: {str(e)}")
            print("  Trying next method...")
    
    # If all methods fail
    print("❌ All create methods failed")
    return None

def _create_asset_oslc(self, siteid, asset_fields):
    """Create asset using OSLC API"""
    # Look for OSLC endpoints
    oslc_endpoints = {k: v for k, v in self.working_endpoints.items() if "oslc" in k}
    if not oslc_endpoints:
        return None
        
    # Choose an OSLC endpoint
    endpoint_name = next(iter(oslc_endpoints.keys()))
    endpoint_info = oslc_endpoints[endpoint_name]
    
    # Get the base URL
    base_url_parts = endpoint_info["url"].split("/os/")
    if len(base_url_parts) < 2:
        print("  Invalid OSLC endpoint URL format")
        return None
        
    # Construct collection URL
    collection_url = f"{base_url_parts[0]}/os/mxasset"
    
    print(f"  Attempting OSLC creation at: {collection_url}")
    
    # Auth headers from the endpoint
    auth_headers = endpoint_info["auth_headers"]
    
    # Prepare OSLC payload with namespace prefixes
    oslc_payload = {
        "spi:siteid": siteid,
    }
    
    # Add asset fields with spi: namespace
    for key, value in asset_fields.items():
        if key != "siteid":  # Skip siteid as we already added it
            if key.startswith("spi:"):
                oslc_payload[key] = value
            else:
                oslc_payload[f"spi:{key}"] = value
    
    # Headers for creation
    create_headers = {
        **auth_headers,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Properties": "siteid," + ",".join(k.replace("spi:", "") for k in oslc_payload 
                              if k != "spi:siteid" and k != "spi:assetnum")
    }
    
    response = requests.post(
        collection_url,
        headers=create_headers,
        json=oslc_payload,
        verify=False,
        timeout=60
    )
    
    if response.status_code in [200, 201]:
        print(f"✅ OSLC creation successful: Status {response.status_code}")
        try:
            # Parse response to get the new asset details
            result = response.json()
            
            # Extract assetnum from various possible response formats
            assetnum = None
            if "rdf:about" in result:
                # Try to extract assetnum from the resource URI
                uri = result["rdf:about"]
                if "assetnum=" in uri:
                    assetnum = uri.split("assetnum=")[1].split("&")[0].strip('"')
                else:
                    # Try to get it from the fields
                    assetnum = result.get("spi:assetnum")
            else:
                # Look for assetnum field with various possible names
                assetnum = result.get("spi:assetnum") or result.get("assetnum")
            
            if assetnum:
                # Return a simple result with at least the asset number
                return {"assetnum": assetnum, "siteid": siteid}
            else:
                # Just return the raw response if we can't find assetnum
                return result
        except Exception as e:
            print(f"  Error parsing OSLC response: {str(e)}")
            return {"success": True, "message": "Asset created but couldn't parse details"}
    else:
        print(f"❌ OSLC creation failed: Status {response.status_code}")
        if response.text:
            print(f"  Response: {response.text[:500]}")
        return None

def _create_asset_rest(self, siteid, asset_fields):
    """Create asset using REST API"""
    # Look for REST endpoints
    rest_endpoints = {k: v for k, v in self.working_endpoints.items() if "rest" in k or "api" in k}
    if not rest_endpoints:
        return None
        
    # Choose a REST endpoint
    endpoint_name = next(iter(rest_endpoints.keys()))
    endpoint_info = rest_endpoints[endpoint_name]
    
    # Get the base URL
    base_url_parts = endpoint_info["url"].split("/os/")
    if len(base_url_parts) < 2:
        # Try another pattern
        base_url_parts = endpoint_info["url"].split("/mbo/")
        if len(base_url_parts) < 2:
            print("  Invalid REST endpoint URL format")
            return None
            
        # Construct API URL
        if "mbo" in endpoint_info["url"]:
            api_url = f"{base_url_parts[0]}/api/os/mxasset"
        else:
            api_url = f"{base_url_parts[0]}/api/os/mxasset"
    else:
        api_url = f"{base_url_parts[0]}/api/os/mxasset"
    
    print(f"  Attempting REST creation at: {api_url}")
    
    # Auth headers from the endpoint
    auth_headers = endpoint_info["auth_headers"]
    
    # Prepare REST payload
    rest_payload = {
        "ASSET": [{
            "SITEID": siteid
        }]
    }
    
    # Add asset fields with uppercase
    for key, value in asset_fields.items():
        if key.lower() != "siteid":  # Skip siteid as we already added it
            rest_payload["ASSET"][0][key.upper()] = value
    
    # Headers for REST
    rest_headers = {
        **auth_headers,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Add action parameter for some Maximo versions
    params = {
        "_action": "Create"
    }
    
    response = requests.post(
        api_url,
        headers=rest_headers,
        params=params,
        json=rest_payload,
        verify=False,
        timeout=60
    )
    
    if response.status_code in [200, 201]:
        print(f"✅ REST creation successful: Status {response.status_code}")
        try:
            result = response.json()
            
            # Try to find assetnum in the response
            assetnum = None
            if "ASSET" in result and isinstance(result["ASSET"], list) and len(result["ASSET"]) > 0:
                assetnum = result["ASSET"][0].get("ASSETNUM")
            elif "member" in result and isinstance(result["member"], list) and len(result["member"]) > 0:
                assetnum = result["member"][0].get("assetnum")
            elif "assetnum" in result:
                assetnum = result.get("assetnum")
            elif "ASSETNUM" in result:
                assetnum = result.get("ASSETNUM")
            
            if assetnum:
                # Return a simple result with at least the asset number
                return {"assetnum": assetnum, "siteid": siteid}
            else:
                # Just return the raw response if we can't find assetnum
                return result
        except Exception as e:
            print(f"  Error parsing REST response: {str(e)}")
            return {"success": True, "message": "Asset created but couldn't parse details"}
    else:
        print(f"❌ REST creation failed: Status {response.status_code}")
        if response.text:
            print(f"  Response: {response.text[:500]}")
        return None

def _create_asset_object_structure(self, siteid, asset_fields):
    """Create asset using object structure API"""
    # Try to construct object structure URL from working endpoints
    os_url = None
    
    for name, info in self.working_endpoints.items():
        base_url = info["url"].split("/os/")[0] if "/os/" in info["url"] else None
        if base_url:
            os_url = f"{base_url}/rest/os/ASSET"
            break
            
    if not os_url:
        for base_url in self.base_urls:
            os_url = f"{base_url}/rest/os/ASSET"
            break
    
    if not os_url:
        return None
        
    print(f"  Attempting object structure creation at: {os_url}")
    
    # Try each auth method
    for auth_name, auth_headers in self.auth_methods.items():
        try:
            # Prepare object structure payload
            os_payload = {
                "SITEID": siteid
            }
            
            # Add asset fields with uppercase
            for key, value in asset_fields.items():
                if key.lower() != "siteid":  # Skip siteid as we already added it
                    os_payload[key.upper()] = value
            
            # Headers for REST
            os_headers = {
                **auth_headers,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            response = requests.post(
                os_url,
                headers=os_headers,
                json=os_payload,
                verify=False,
                timeout=60
            )
            
            if response.status_code in [200, 201]:
                print(f"✅ Object structure creation successful: Status {response.status_code}")
                try:
                    result = response.json()
                    
                    # Try to find assetnum in the response
                    assetnum = result.get("ASSETNUM") or result.get("assetnum")
                    
                    if assetnum:
                        # Return a simple result with at least the asset number
                        return {"assetnum": assetnum, "siteid": siteid}
                    else:
                        # Just return the raw response if we can't find assetnum
                        return result
                except Exception as e:
                    print(f"  Error parsing object structure response: {str(e)}")
                    return {"success": True, "message": "Asset created but couldn't parse details"}
            else:
                print(f"❌ Object structure creation with {auth_name} failed: Status {response.status_code}")
                if response.text:
                    print(f"  Response: {response.text[:200]}")
        except Exception as e:
            print(f"  Error with object structure creation using {auth_name}: {str(e)}")
            
    return None
