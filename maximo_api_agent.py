import os
import requests
import json
import argparse
import urllib3
from urllib.parse import urlencode

# Suppress only the single InsecureRequestWarning from urllib3 needed for self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
# For development, you can hardcode them here.
# For command-line execution, it is recommended to use environment variables.
# Example: set MAXIMO_HOST=https://your.maximo.com
MAXIMO_HOST = os.environ.get("MAXIMO_HOST", "YOUR_MAXIMO_HOST_HERE")
API_KEY = os.environ.get("MAXIMO_API_KEY", "YOUR_MAXIMO_API_KEY_HERE")

class MaximoAPIClient:
    """
    A client for interacting with the IBM Maximo JSON API.
    This version uses URL parameters for authentication for simplicity and compatibility.
    """
    def __init__(self, host, api_key):
        if not host or "your.maximo.com" in host:
            raise ValueError(f"MAXIMO_HOST is not configured correctly. The value received was '{host}'. Please set it as an environment variable or hardcode it in the script.")
        if not api_key or "your_long_api_key" in api_key or "apikey" == api_key:
             raise ValueError(f"API_KEY is not configured correctly. The value received was empty or is still a placeholder. Please set it as an environment variable or hardcode it in the script.")
        
        self.host = host
        self.api_key = api_key
        # Some Maximo servers require an explicit "Accept" header to avoid a 406 error.
        # We define it here to be used in all requests.
        # The API key is also placed here for secure header-based authentication.
        self.headers = {
            "Accept": "application/json",
            "apikey": self.api_key
        }

    def test_connection(self):
        """Test connection using mxperson"""
        url = f"{self.host}/maximo/api/os/mxperson"
        # To align with the working get_asset function, we use the more modern
        # OSLC parameters instead of the simpler '_limit'.
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
        Retrieves details for one or more assets.
        """
        url = f"{self.host}/maximo/api/os/mxasset"

        # Handle single or multiple asset numbers by building the correct WHERE clause.
        if "," in assetnum:
            # Create a list of quoted asset numbers for the IN clause
            asset_list = [f'"{a.strip()}"' for a in assetnum.split(',')]
            where_clause = f'assetnum in [{",".join(asset_list)}]'
        else:
            where_clause = f'assetnum="{assetnum.strip()}"'

        if siteid:
            where_clause += f' and siteid="{siteid}"'

        # Default fields if none are provided, otherwise use the requested fields.
        # This makes the function backward-compatible.
        select_fields = "assetnum,description,status"
        if fields_to_select:
            # Ensure assetnum is always included for data consistency
            if "assetnum" not in fields_to_select.lower().split(','):
                select_fields = "assetnum," + fields_to_select
            else:
                select_fields = fields_to_select

        params = {
            "oslc.where": where_clause,
            "oslc.select": select_fields, # Use the dynamic or default fields
            "lean": 1,
            # "oslc.pageSize": 1, # Removed to allow multiple records to be returned
            "_format": "json"
        }

        print("--> Final URL being requested (with encoded params):", f"{url}?{urlencode(params)}")

        try:
            response = requests.get(url, params=params, headers=self.headers, verify=False, timeout=15)

            if response.ok:
                data = response.json()
                if "member" in data and data.get("member"):
                    assets = data["member"]
                    
                    requested_fields = select_fields.split(',')
                    
                    # Process all returned assets into a list of clean dictionaries
                    clean_assets = []
                    for asset in assets:
                        clean_asset = {field: asset.get(field) for field in requested_fields if field in asset}
                        clean_assets.append(clean_asset)
                    return clean_assets
                else:
                    # No assets found, return an empty list
                    return []
            else:
                print(f"❌ API Error while fetching asset: {response.status_code} - {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ CRITICAL: A network error occurred while fetching asset.\n   Error: {e}")
            return None

    def update_asset(self, assetnum: str, siteid: str, fields_to_update: dict) -> dict | None:
        """
        Updates one or more fields for an existing asset using a POST with a PATCH override.
        """
        print(f"Attempting to update asset '{assetnum}' at site '{siteid}' with data: {fields_to_update}...")

        # Step 1: Get the unique href (URL) for the specific asset record.
        where_clause = f'assetnum="{assetnum}" and siteid="{siteid}"'

        asset_href = self._get_record_href("mxasset", where_clause)
        
        if not asset_href:
            print(f"❌ Could not find asset '{assetnum}' at site '{siteid}' to update.")
            return None

        # Step 2: Prepare and send the PATCH request to the asset's unique URL.
        update_url = asset_href
        
        # The normal headers already have Accept and apikey. We add the override header.
        patch_headers = self.headers.copy()
        patch_headers["x-method-override"] = "PATCH"
        patch_headers["patchtype"] = "MERGE" # MERGE only updates specified fields.

        payload = fields_to_update

        print(f"--> Sending PATCH request to: {update_url}")

        try:
            response = requests.post(update_url, headers=patch_headers, json=payload, verify=False, timeout=15)

            # A 204 No Content status code means the API call was accepted.
            # It does NOT guarantee the business logic was successful.
            if response.status_code == 204:
                # --- Verification Step ---
                # The API call was accepted, but let's verify the status actually changed.
                print("--> Update command accepted by Maximo. Now verifying the change...")
                fields_to_verify = ",".join(fields_to_update.keys())
                verified_asset_list = self.get_asset(assetnum, siteid, fields_to_select=fields_to_verify)

                # The get_asset function now returns a list. We need to check the first item.
                if verified_asset_list:
                    asset_details = verified_asset_list[0]
                    mismatched_fields = []
                    for key, value in fields_to_update.items():
                        # Compare as strings for robustness against type differences (e.g., int vs float)
                        if str(asset_details.get(key)) != str(value):
                            mismatched_fields.append(f"Field '{key}' is still '{asset_details.get(key)}', not '{value}'.")
                    
                    if not mismatched_fields:
                        return {"status": "success", "message": f"Asset {assetnum} successfully updated.", "updated_fields": fields_to_update}
                    else:
                        print(f"❌ VERIFICATION FAILED: The following fields did not update correctly:")
                        for mismatch in mismatched_fields:
                            print(f"    - {mismatch}")
                        print("    This usually means the user associated with the API key lacks permission for this specific change, or a business rule prevented it.")
                        return None
                else:
                    # This case would be rare, as we just found the asset to get its href.
                    print(f"❌ VERIFICATION FAILED: Could not re-fetch asset '{assetnum}' after update attempt.")
                    return None
            else:
                print(f"❌ Failed to update asset: {response.status_code}")
                print(response.text)
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ CRITICAL: A network error occurred while updating asset.\n   Error: {e}")
            return None

    def _get_record_href(self, object_structure, where_clause):
        """Helper function to get a record's unique URL (href) for updates."""
        url = f"{self.host}/maximo/api/os/{object_structure}"
        params = {"oslc.where": where_clause, "oslc.select": "href", "lean": 1, "_format": "json"}
        try:
            response = requests.get(url, params=params, headers=self.headers, verify=False, timeout=10)
            if response.ok:
                data = response.json()
                if data.get('member') and data['member']:
                    return data['member'][0].get('href')
        except requests.exceptions.RequestException:
            return None # The calling function will handle the error message.
        return None

def main():
    """
    Main function to provide a command-line interface for the MaximoClient.
    """
    parser = argparse.ArgumentParser(description="A command-line agent to interact with the Maximo API.")
    parser.add_argument("action", choices=['get-asset', 'test-connection', 'update-asset'], help="The action to perform.")
    parser.add_argument("--assetnum", help="Asset number for 'get-asset' or 'update-asset'.")
    parser.add_argument("--fields", help="For 'update-asset', a JSON string of fields to update, e.g., '{\"description\":\"new desc\"}'.")
    parser.add_argument("--siteid", help="The site ID for the record (e.g., BEDFORD).")

    args = parser.parse_args()

    try:
        client = MaximoAPIClient(host=MAXIMO_HOST, api_key=API_KEY)

        # If the action is just to test the connection, do that and exit.
        if args.action == 'test-connection':
            result = client.test_connection()
            if result:
                print("✅ Connection and authentication successful!")
                if result.get('member'):
                    print(f"--> Successfully fetched 1 person record: {result['member'][0].get('personid', 'N/A')}")
                print("\n--- Test Result ---")
                print(json.dumps(result, indent=2))
            else:
                print("❌ Connection test failed. Check terminal for specific errors.")

        elif args.action == 'get-asset':
            if not args.assetnum:
                parser.error("--assetnum is required for the 'get-asset' action.")
            asset = client.get_asset(args.assetnum, siteid=args.siteid)
            if asset:
                print("✅ Asset retrieved successfully!")
                print("\n--- Asset Details ---\n", json.dumps(asset, indent=2))
            else:
                print(f"❌ Failed to retrieve asset '{args.assetnum}' or it was not found. Check terminal for specific errors.")

        elif args.action == 'update-asset':
            if not all([args.assetnum, args.siteid, args.fields]):
                parser.error("--assetnum, --siteid, and --fields are required for the 'update-asset' action.")
            try:
                fields_to_update = json.loads(args.fields)
            except json.JSONDecodeError:
                parser.error("--fields must be a valid JSON string.")

            result = client.update_asset(args.assetnum, args.siteid, fields_to_update)
            if result:
                print("✅ Asset updated successfully!")
                print("\n--- Update Result ---\n", json.dumps(result, indent=2))
            else:
                print(f"❌ Failed to update asset '{args.assetnum}'. Check terminal for specific errors.")

    except ValueError as e:
        print(f"Configuration Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
