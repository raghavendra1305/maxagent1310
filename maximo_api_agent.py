import os
import requests
import json
import argparse
import urllib3
from urllib.parse import urlencode
import base64
import logging

# Suppress only the single InsecureRequestWarning from urllib3 needed for self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
# For development, you can hardcode them here.
# For command-line execution, it is recommended to use environment variables.
MAXIMO_HOST = os.environ.get("MAXIMO_HOST")
API_KEY = os.environ.get("MAXIMO_API_KEY")
MAXIMO_USER = os.environ.get("MAXIMO_USER")
MAXIMO_PASSWORD = os.environ.get("MAXIMO_PASSWORD")

class MaximoAPIClient:
    """
    A client for interacting with the IBM Maximo JSON API.
    This version supports both API Key and Basic (maxauth) authentication.
    """
    def __init__(self, host, api_key=None, user=None, password=None):
        if not host:
            raise ValueError("Maximo Host must be provided.")
        
        if api_key:
            self.auth_header = {"apikey": api_key}
        elif user and password:
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.auth_header = {"maxauth": credentials}
        else:
            raise ValueError("Either API key or username/password must be provided.")
        
        self.host = host
        
        # Common headers for all GET requests
        self.get_headers = {
            **self.auth_header,
            "Accept": "application/json",
        }
        # Common headers for all POST/PATCH update requests
        self.update_headers = {
            **self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json"
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
            response = requests.get(url, params=params, headers=self.get_headers, verify=False, timeout=10)
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
        resource_url = f"{self.host}/maximo/oslc/os/mxasset"

        # Handle single or multiple asset numbers by building the correct WHERE clause.
        if "," in assetnum:
            # Create a list of quoted asset numbers for the IN clause
            asset_list = [f'"{a.strip()}"' for a in assetnum.split(',')]
            where_clause = f'assetnum in [{",".join(asset_list)}]'
        else:
            where_clause = f'spi:assetnum="{assetnum.strip()}"'

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
            "oslc.select": select_fields
        }

        print("--> Final URL being requested (with encoded params):", f"{resource_url}?{urlencode(params)}")

        try:
            response = requests.get(resource_url, params=params, headers=self.get_headers, verify=False, timeout=15)

            if response.ok:
                data = response.json()
                
                # Handle different response formats for members
                members = data.get("member", data.get("rdfs:member"))

                if members:
                    assets = members
                    
                    requested_fields = select_fields.split(',')
                    
                    # Process all returned assets into a list of clean dictionaries
                    clean_assets = []
                    for asset in assets:
                        # OSLC API often prefixes fields with spi:
                        clean_asset = {field: asset.get(f"spi:{field}", asset.get(field)) for field in requested_fields.split(',')}
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

    def get_work_orders(self, where_clause: str, fields_to_select: str = "wonum,description,status,worktype") -> list | None:
        """
        Retrieves a list of work orders based on a given where clause.
        """
        url = f"{self.host}/maximo/api/os/mxwo" # Using the mxwo object structure

        params = {
            "oslc.where": where_clause,
            "oslc.select": fields_to_select,
            "lean": 1,
            "_format": "json"
        }

        print("--> Fetching work orders with URL:", f"{url}?{urlencode(params)}")

        try:
            response = requests.get(url, params=params, headers=self.headers, verify=False, timeout=30)

            if response.ok:
                data = response.json()
                if "member" in data and data.get("member"):
                    # The data is already a list of dictionaries, so we can return it directly.
                    return data["member"]
                else:
                    # No work orders found
                    return []
            else:
                print(f"❌ API Error while fetching work orders: {response.status_code} - {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ CRITICAL: A network error occurred while fetching work orders.\n   Error: {e}")
            return None

    def update_asset(self, assetnum: str, siteid: str, fields_to_update: dict) -> dict | None:
        """
        Updates an asset using Maximo OSLC API with direct resource targeting.
        """
        print(f"Attempting to update asset '{assetnum}' at site '{siteid}' with data: {fields_to_update}...")

        try:
            # Step 1: Get the specific asset to find its URI
            print("--> Step 1: Looking up the asset to get its URI...")
            asset_list = self.get_asset(assetnum, siteid)
            if not asset_list:
                raise Exception(f"Asset {assetnum} at site {siteid} not found.")
            
            asset_data = asset_list[0]
            asset_uri = asset_data.get("href", asset_data.get("rdf:about"))

            if not asset_uri:
                raise Exception("Could not find asset URI ('href') in the response.")
            
            print(f"--> Found asset URI: {asset_uri}")

            # Step 2: Prepare the update payload with OSLC namespace prefixes
            payload = {}
            for key, value in fields_to_update.items():
                payload[f"spi:{key}"] = value

            # Step 3: Prepare headers for the PATCH request
            headers = self.update_headers.copy()
            headers["x-method-override"] = "PATCH"
            # The 'Properties' header tells Maximo which fields are being updated.
            headers["Properties"] = ",".join(fields_to_update.keys())

            print(f"--> Step 2: Sending PATCH request to update asset...")
            response = requests.post(
                asset_uri,
                headers=headers,
                json=payload,
                verify=False,
                timeout=15
            )

            if response.status_code in [200, 201, 204]:
                success_msg = f"Successfully updated asset {assetnum} at site {siteid}"
                print(f"--> {success_msg}")
                return {"status": "success", "message": success_msg, "updated_fields": fields_to_update}
            else:
                error_msg = f"Failed to update asset. Status: {response.status_code}, Body: {response.text}"
                print(f"❌ {error_msg}")
                raise Exception(error_msg)

        except requests.exceptions.RequestException as e:
            print(f"❌ CRITICAL: A network error occurred while updating asset.\n   Error: {e}")
            return None

def main():
    """
    Main function to provide a command-line interface for the MaximoAPIClient.
    """
    parser = argparse.ArgumentParser(description="A command-line agent to interact with the Maximo API.")
    parser.add_argument("action", choices=['get-asset', 'test-connection', 'update-asset'], help="The action to perform.")
    parser.add_argument("--assetnum", help="Asset number for 'get-asset' or 'update-asset'.")
    parser.add_argument("--fields", help="For 'update-asset', a JSON string of fields to update, e.g., '{\"description\":\"new desc\"}'.")
    parser.add_argument("--siteid", help="The site ID for the record (e.g., BEDFORD).")

    args = parser.parse_args()

    try:
        # Initialize client with API Key first, with fallback to User/Pass
        if API_KEY and API_KEY != "YOUR_MAXIMO_API_KEY_HERE":
            client = MaximoAPIClient(host=MAXIMO_HOST, api_key=API_KEY)
        elif MAXIMO_USER and MAXIMO_PASSWORD:
            client = MaximoAPIClient(host=MAXIMO_HOST, user=MAXIMO_USER, password=MAXIMO_PASSWORD)
        else:
            raise ValueError("No valid Maximo credentials found in environment variables (MAXIMO_API_KEY or MAXIMO_USER/MAXIMO_PASSWORD).")

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
