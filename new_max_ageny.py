import os
import requests
import json
import argparse
import urllib3
import time
import base64
from urllib.parse import urlencode
from tabulate import tabulate

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
                "oslc.select": "*", # Request all fields to ensure we get everything needed
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
                "oslc.select": "*", # Request all fields to ensure we get everything needed
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
        
        # If OSLC API failed, try the standard REST API (implementation similar to get_asset)
        try:
            # Implementation follows similar pattern to the get_asset REST fallback
            #where_clause = f'location="{location.strip()}"' if "," not in location else f'location in [{",".join([f\'"{l.strip()}"\' for l in location.split(",")])}]'
            where_clause = f'location="{location.strip()}"' if "," not in location else f'location in [{",".join([f"\"{l.strip()}\"" for l in location.split(",")])}]'
            if siteid:
                where_clause += f' and siteid="{siteid}"'
            
            # Rest of implementation similar to get_asset
            # ...
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
        
        # Verify the update if successful - similar to update_asset verification
        # ...
        
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
    
    def _get_record_href(self, object_structure, where_clause):
        """Helper function to get a record's unique URL (href) for updates."""
        url = f"{self.api_url}/{object_structure}"
        params = {"oslc.where": where_clause, "oslc.select": "href", "lean": 1, "_format": "json"}
        try:
            response = requests.get(url, params=params, headers=self.headers, verify=False, timeout=10)
            if response.ok:
                data = response.json()
                if data.get('member') and data['member']:
                    return data['member'][0].get('href')
                # Also check for OSLC response format
                elif data.get('rdfs:member') and data['rdfs:member']:
                    member = data['rdfs:member'][0]
                    return member.get('rdf:about') or member.get('href')
        except requests.exceptions.RequestException:
            return None  # The calling function will handle the error message.
        return None
######################################
    def create_asset(self, siteid, asset_data):
        """
    Creates a new asset in Maximo with auto-generated asset number.
    Handles both autonumber and manual numbering scenarios.
    
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
        
        # Try different creation methods
        success = False
        created_assetnum = None
        response_data = None
        
        # Method 1: OSLC API WITHOUT assetnum (for autonumber)
        try:
            print(f"\n  Method 1: OSLC API (autonumber mode)...")
            
            # Use the OSLC endpoint
            oslc_url = f"{self.oslc_url}/mxasset"
            
            # Prepare payload with spi: prefixes but NO assetnum
            oslc_payload = {
                "spi:siteid": siteid
            }
            
            # Add other fields with spi: prefix
            for key, value in create_fields.items():
                if key.lower() not in ["siteid", "assetnum"]:  # Exclude assetnum
                    oslc_payload[f"spi:{key}"] = value
            
            # Headers for creation
            create_headers = {
                **self.json_headers,
                "Properties": "*"
            }
            
            print(f"  URL: {oslc_url}")
            print(f"  Payload: {json.dumps(oslc_payload)}")
            
            response = requests.post(
                oslc_url,
                headers=create_headers,
                json=oslc_payload,
                verify=False,
                timeout=60
            )
            
            if response.status_code in [200, 201]:
                print(f"✅ OSLC creation successful: Status {response.status_code}")
                response_data = response.json()
                success = True
            else:
                print(f"  OSLC creation failed: Status {response.status_code}")
                if response.text:
                    print(f"  Response: {response.text[:300]}")
        except Exception as e:
            print(f"  Method 1 error: {str(e)}")
        
        # Method 2: REST API without assetnum
        if not success:
            try:
                print(f"\n  Method 2: REST API (autonumber mode)...")
                
                # Prepare payload WITHOUT assetnum
                rest_payload = {
                    "ASSET": [{
                        "SITEID": siteid
                        # NO ASSETNUM field
                    }]
                }
                
                # Add other fields in uppercase
                for key, value in create_fields.items():
                    if key.lower() not in ["siteid", "assetnum"]:
                        rest_payload["ASSET"][0][key.upper()] = value
                
                # Try with Add action
                params = {
                    "_action": "Add",
                    "lean": 1
                }
                
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
                
                if response.status_code in [200, 201]:
                    print(f"✅ REST API creation successful: Status {response.status_code}")
                    response_data = response.json()
                    success = True
                else:
                    print(f"  REST API creation failed: Status {response.status_code}")
                    if response.text:
                        print(f"  Response: {response.text[:300]}")
            except Exception as e:
                print(f"  Method 2 error: {str(e)}")
        
        # Method 3: Direct POST without wrapper and without assetnum
        if not success:
            try:
                print(f"\n  Method 3: Direct POST (autonumber mode)...")
                
                # Simple payload structure WITHOUT assetnum
                direct_payload = {
                    "siteid": siteid
                }
                
                # Add other fields
                for key, value in create_fields.items():
                    if key.lower() not in ["siteid", "assetnum"]:
                        direct_payload[key.lower()] = value
                
                print(f"  URL: {self.api_url}/mxasset")
                print(f"  Payload: {json.dumps(direct_payload)}")
                
                response = requests.post(
                    f"{self.api_url}/mxasset",
                    headers=self.json_headers,
                    json=direct_payload,
                    verify=False,
                    timeout=60
                )
                
                if response.status_code in [200, 201]:
                    print(f"✅ Direct POST successful: Status {response.status_code}")
                    response_data = response.json()
                    success = True
                else:
                    print(f"  Direct POST failed: Status {response.status_code}")
                    if response.text:
                        print(f"  Response: {response.text[:300]}")
            except Exception as e:
                print(f"  Method 3 error: {str(e)}")
        
        # Parse response to get asset number
        if success and response_data:
            try:
                print(f"\n  Parsing response to find asset number...")
                print(f"  Response type: {type(response_data)}")
                
                # Try different response formats
                if isinstance(response_data, dict):
                    # Direct response
                    created_assetnum = response_data.get("assetnum") or response_data.get("ASSETNUM")
                    
                    # OSLC response with spi: prefix
                    if not created_assetnum:
                        created_assetnum = response_data.get("spi:assetnum")
                    
                    # Check if it's in a member array
                    if not created_assetnum and "member" in response_data:
                        if response_data["member"] and len(response_data["member"]) > 0:
                            member = response_data["member"][0]
                            created_assetnum = (member.get("assetnum") or 
                                            member.get("ASSETNUM") or 
                                            member.get("spi:assetnum"))
                    
                    # Check if it's in an ASSET array
                    if not created_assetnum and "ASSET" in response_data:
                        if response_data["ASSET"] and len(response_data["ASSET"]) > 0:
                            asset = response_data["ASSET"][0]
                            created_assetnum = asset.get("ASSETNUM") or asset.get("assetnum")
                    
                    # From resource URI (rdf:about)
                    if not created_assetnum and "rdf:about" in response_data:
                        uri = response_data["rdf:about"]
                        print(f"  Found resource URI: {uri}")
                        # Extract from patterns like _MTMxNTAvQkVERk9SRA--
                        if "_" in uri and "/" in uri:
                            try:
                                # Get the encoded part
                                parts = uri.split("/")
                                for part in parts:
                                    if part.startswith("_") and part.endswith("--"):
                                        encoded = part[1:-2]  # Remove _ and --
                                        # Decode base64
                                        decoded = base64.b64decode(encoded + "==").decode('utf-8')
                                        # Format is usually "assetnum/siteid"
                                        decoded_parts = decoded.split("/")
                                        if decoded_parts[0] and decoded_parts[0] != "*":
                                            created_assetnum = decoded_parts[0]
                                            print(f"  Decoded asset number from URI: {created_assetnum}")
                                        break
                            except Exception as e:
                                print(f"  Error decoding URI: {str(e)}")
                
                elif isinstance(response_data, list) and len(response_data) > 0:
                    # Response is a direct array
                    first_item = response_data[0]
                    created_assetnum = (first_item.get("assetnum") or 
                                    first_item.get("ASSETNUM") or 
                                    first_item.get("spi:assetnum"))
                
                if created_assetnum and created_assetnum != "*":
                    print(f"✅ Asset created with number: {created_assetnum}")
                else:
                    print("  Could not find asset number in response")
                    created_assetnum = None
                    
            except Exception as e:
                print(f"  Error parsing response: {str(e)}")
        
        # If successful but no asset number, try to find it
        if success and not created_assetnum:
            try:
                print("\n  Searching for newly created asset...")
                time.sleep(3)  # Wait for database commit
                
                # Build search criteria
                search_where = f'siteid="{siteid}"'
                
                # If we have a unique field like description, use it
                if "description" in create_fields and create_fields["description"]:
                    search_where += f' and description="{create_fields["description"]}"'
                
                search_params = {
                    "oslc.where": search_where,
                    "oslc.select": "assetnum,siteid,description,changedate,assetid",
                    "oslc.orderBy": "-assetid",  # Order by asset ID descending (newest first)
                    "oslc.pageSize": "5",
                    "_format": "json"
                }
                
                print(f"  Search criteria: {search_where}")
                
                response = requests.get(
                    f"{self.api_url}/mxasset",
                    headers=self.headers,
                    params=search_params,
                    verify=False,
                    timeout=15
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if "member" in data and data["member"] and len(data["member"]) > 0:
                        # Look for the asset we just created
                        for asset in data["member"]:
                            # Check if this matches our creation data
                            match = True
                            for key, value in create_fields.items():
                                asset_value = asset.get(key.lower())
                                if asset_value and str(asset_value).lower() != str(value).lower():
                                    match = False
                                    break
                            
                            if match:
                                created_assetnum = asset.get("assetnum")
                                if created_assetnum:
                                    print(f"✅ Found newly created asset: {created_assetnum}")
                                    break
                        
                        # If no exact match, take the newest one
                        if not created_assetnum:
                            created_assetnum = data["member"][0].get("assetnum")
                            if created_assetnum:
                                print(f"✅ Found newest asset (assumed to be ours): {created_assetnum}")
                                
            except Exception as e:
                print(f"  Error searching for asset: {str(e)}")
        
        # Return results
        if not success:
            print("\n❌ All creation methods failed")
            print("💡 Possible issues:")
            print("  1. Site ID might not be valid or active")
            print("  2. User permissions for asset creation")
            print("  3. Required fields missing")
            print("  4. Workflow or automation scripts blocking creation")
            return None
        
        # Build return data
        result = {
            "status": "success",
            "siteid": siteid,
            **create_fields
        }
        
        if created_assetnum:
            result["assetnum"] = created_assetnum
            result["message"] = f"Asset {created_assetnum} created successfully"
            
            # Try to get full details
            try:
                print(f"\n🔍 Retrieving full details for asset {created_assetnum}")
                time.sleep(1)
                full_assets = self.get_asset(created_assetnum, siteid)
                if full_assets and len(full_assets) > 0:
                    full_asset = full_assets[0]
                    full_asset["_message"] = f"Asset {created_assetnum} created successfully"
                    return full_asset
            except Exception as e:
                print(f"  Could not retrieve full details: {str(e)}")
        else:
            result["message"] = "Asset created successfully but couldn't determine asset number"
            result["status"] = "partial_success"
        
        return result
   

# Add these methods to your MaximoAPIClient class:

def list_assets(self, search_criteria=None, fields_to_select=None, max_results=100):
    """
    Lists assets based on search criteria.
    
    Args:
        search_criteria (str or dict): Search conditions like {"status": "ACTIVE"} or OSLC where clause
        fields_to_select (str): Comma-separated list of fields to return
        max_results (int): Maximum number of results to return (default 100)
        
    Returns:
        list: List of assets matching the criteria
    """
    print(f"\n📋 Listing assets with criteria: {search_criteria}")
    
    # Build the where clause
    where_clause = ""
    
    if search_criteria:
        if isinstance(search_criteria, str):
            # Direct OSLC where clause provided
            where_clause = search_criteria
        elif isinstance(search_criteria, dict):
            # Build where clause from dictionary
            conditions = []
            for field, value in search_criteria.items():
                # Handle different field types
                if isinstance(value, str):
                    # Check if it's a wildcard search
                    if '*' in value:
                        conditions.append(f'{field} like "{value.replace("*", "%")}"')
                    else:
                        conditions.append(f'{field}="{value}"')
                elif isinstance(value, (int, float)):
                    conditions.append(f'{field}={value}')
                elif isinstance(value, bool):
                    conditions.append(f'{field}={str(value).lower()}')
                elif isinstance(value, list):
                    # Handle IN clause
                    values_str = ','.join([f'"{v}"' if isinstance(v, str) else str(v) for v in value])
                    conditions.append(f'{field} in [{values_str}]')
                elif value is None:
                    conditions.append(f'{field} is null')
            
            where_clause = ' and '.join(conditions)
    
    # Default fields if none specified
    if not fields_to_select:
        fields_to_select = "assetnum,siteid,description,status,location,assettype"
    
    print(f"  Where clause: {where_clause}")
    print(f"  Fields: {fields_to_select}")
    print(f"  Max results: {max_results}")
    
    # Try OSLC API first
    try:
        params = {
            "oslc.select": "*",  # Get all fields for proper filtering
            "oslc.pageSize": str(max_results),
            "_format": "json",
            "_ts": int(time.time())
        }
        
        # Add where clause if provided
        if where_clause:
            # Add spi: prefix for OSLC
            oslc_where = where_clause
            # Replace field names with spi: prefix if not already present
            for field in ['status', 'siteid', 'description', 'location', 'assettype', 'assetnum']:
                if field in oslc_where and f'spi:{field}' not in oslc_where:
                    oslc_where = oslc_where.replace(field, f'spi:{field}')
            params["oslc.where"] = oslc_where
        
        oslc_url = f"{self.oslc_url}/mxasset"
        print(f"\n  Trying OSLC API...")
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
            
            # Handle response
            members = None
            if "member" in data:
                members = data["member"]
            elif "rdfs:member" in data:
                members = data["rdfs:member"]
            
            if members:
                print(f"✅ Found {len(members)} assets via OSLC API")
                
                # Clean up the response
                clean_assets = []
                fields_list = fields_to_select.split(',')
                
                for asset in members:
                    clean_asset = {}
                    
                    # Extract requested fields
                    for field in fields_list:
                        field = field.strip()
                        
                        # Check both prefixed and non-prefixed
                        if field in asset:
                            clean_asset[field] = asset[field]
                        elif f"spi:{field}" in asset:
                            clean_asset[field] = asset[f"spi:{field}"]
                        # Case insensitive check
                        else:
                            for k in asset.keys():
                                if k.lower() == field.lower() or k.lower() == f"spi:{field}".lower():
                                    clean_asset[field] = asset[k]
                                    break
                    
                    clean_assets.append(clean_asset)
                
                return clean_assets
            else:
                print("  No assets found matching criteria")
                return []
                
    except Exception as e:
        print(f"  OSLC API error: {str(e)}")
    
    # Try REST API as fallback
    try:
        params = {
            "oslc.select": fields_to_select,
            "oslc.pageSize": str(max_results),
            "lean": 1,
            "_format": "json",
            "_ts": int(time.time())
        }
        
        if where_clause:
            params["oslc.where"] = where_clause
        
        rest_url = f"{self.api_url}/mxasset"
        print(f"\n  Trying REST API...")
        print(f"  URL: {rest_url}")
        
        response = requests.get(
            rest_url,
            headers=self.headers,
            params=params,
            verify=False,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "member" in data and data.get("member"):
                assets = data["member"]
                print(f"✅ Found {len(assets)} assets via REST API")
                return assets
            else:
                print("  No assets found matching criteria")
                return []
                
    except Exception as e:
        print(f"  REST API error: {str(e)}")
    
    print("❌ Failed to retrieve asset list")
    return []


def list_assets_table(self, search_criteria=None, fields_to_display=None, max_results=100):
    """
    Lists assets in a table format with any fields you specify.
    
    Args:
        search_criteria (str or dict): Search conditions like {"status": "ACTIVE"}
        fields_to_display (str): Comma-separated list of fields to show in table
        max_results (int): Maximum number of results (default 100)
        
    Returns:
        dict: Contains formatted table and raw data
    """
    print(f"\n📊 Listing assets in table format")
    print(f"  Search criteria: {search_criteria}")
    print(f"  Fields to display: {fields_to_display}")
    print(f"  Max results: {max_results}")
    
    # Default fields if none specified
    if not fields_to_display:
        fields_to_display = "assetnum,status,siteid,description"
    
    # Get the assets using existing list_assets method
    # We need to get all fields to ensure we can display what user asks for
    assets = self.list_assets(
        search_criteria=search_criteria,
        fields_to_select="*",  # Get all fields
        max_results=max_results
    )
    
    if not assets:
        return {
            "message": "No assets found matching the criteria",
            "table": "No data to display",
            "count": 0
        }
    
    # Parse the fields to display
    fields_list = [f.strip() for f in fields_to_display.split(',')]
    
    # Prepare data for table
    table_data = []
    for asset in assets:
        row = []
        for field in fields_list:
            # Get the field value, handle missing fields
            value = asset.get(field, 'N/A')
            
            # Format the value
            if value is None:
                value = 'N/A'
            elif isinstance(value, str) and len(str(value)) > 50:
                value = str(value)[:47] + '...'
            elif isinstance(value, bool):
                value = 'Yes' if value else 'No'
            
            row.append(value)
        table_data.append(row)
    
    # Create headers (capitalize and format nicely)
    headers = []
    for field in fields_list:
        # Convert field names to readable headers
        header = field.upper().replace('_', ' ')
        # Special formatting for common fields
        if field.lower() == 'assetnum':
            header = 'ASSET#'
        elif field.lower() == 'siteid':
            header = 'SITE'
        elif field.lower() == 'serialnum':
            header = 'SERIAL#'
        headers.append(header)
    
    # Generate the table
    table_text = tabulate(
        table_data, 
        headers=headers, 
        tablefmt="grid",  # You can change this to "simple", "pretty", etc.
        numalign="left"   # Left align numbers for consistency
    )
    
    # Add summary
    summary = f"\n\nTotal: {len(assets)} assets found"
    if len(assets) == max_results:
        summary += f" (limited to {max_results})"
    
    # Combine table and summary
    full_table = table_text + summary
    
    print("\n" + full_table)
    
    return {
        "table": full_table,
        "count": len(assets),
        "fields": fields_list,
        "data": assets  # Include raw data if needed
    }
