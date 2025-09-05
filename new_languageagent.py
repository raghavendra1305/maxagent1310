import google.generativeai as genai
import json

# Updated tool definitions to match the MaximoAPIClient methods
MAXIMO_TOOLS = [
    {
        "name": "get_asset",
        "description": "Retrieves details for one or more assets from Maximo. You can specify which fields to return.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assetnum": {
                    "type": "STRING",
                    "description": "The unique identifier for the asset. For multiple assets, provide a comma-separated list, e.g., '11430,11431'."
                },
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the asset, e.g., 'BEDFORD'."
                },
                "fields_to_select": {
                    "type": "STRING",
                    "description": "A comma-separated list of fields to retrieve, e.g., 'assetnum,description,status,assettype,location,calnum'."
                }
            },
            "required": ["assetnum"]
        }
    },
    {
        "name": "update_asset",
        "description": "Updates one or more fields for an existing asset in Maximo. Can update any field including description, status, location, assettype, etc.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assetnum": {
                    "type": "STRING",
                    "description": "The unique identifier for the asset to be updated."
                },
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the asset. This is required for an update."
                },
                "fields_to_update": {
                    "type": "STRING",
                    "description": "A JSON formatted string representing the fields to update. Example: '{\"description\": \"New description\", \"status\": \"ACTIVE\", \"assettype\": \"BUS\"}'"
                }
            },
            "required": ["assetnum", "siteid", "fields_to_update"]
        }
    },
    {
        "name": "create_asset",
        "description": "Creates a new asset in Maximo. The asset number will be auto-generated. Site ID is mandatory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the new asset. This is required."
                },
                "asset_data": {
                    "type": "STRING",
                    "description": "A JSON formatted string with asset fields. Example: '{\"description\": \"Pump failure\", \"assettype\": \"BUS\", \"location\": \"LOC123\"}'"
                }
            },
            "required": ["siteid", "asset_data"]
        }
    },
    {
        "name": "get_location",
        "description": "Retrieves details for one or more locations from Maximo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "The location ID. For multiple locations, provide a comma-separated list."
                },
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the location."
                },
                "fields_to_select": {
                    "type": "STRING",
                    "description": "A comma-separated list of fields to retrieve."
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "update_location",
        "description": "Updates one or more fields for an existing location in Maximo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "The location ID to update."
                },
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the location."
                },
                "fields_to_update": {
                    "type": "STRING",
                    "description": "A JSON formatted string representing the fields to update."
                }
            },
            "required": ["location", "fields_to_update"]
        }
    },
    {
        "name": "test_connection",
        "description": "Tests the connection and authentication to the Maximo server.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "list_assets_table",
        "description": "Lists assets in a formatted table. You can specify any fields to display just like 'assetnum,status,siteid' or 'assetnum,description,location,manufacturer'",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search_criteria": {
                    "type": "STRING",
                    "description": "JSON search criteria. Examples: '{\"status\": \"ACTIVE\"}' or '{\"status\": \"ACTIVE\", \"siteid\": \"BEDFORD\"}'"
                },
                "fields_to_display": {
                    "type": "STRING",
                    "description": "Comma-separated list of fields to show in the table. Example: 'assetnum,status,siteid,description,location'"
                },
                "max_results": {
                    "type": "INTEGER",
                    "description": "Maximum number of rows to return. Default is 100."
                }
            },
            "required": []
        }
    }
]

def get_maximo_tool_call(user_prompt: str, api_key: str):
    """
    Uses the Gemini API with function calling to determine which Maximo tool to use.
    """
    try:
        genai.configure(api_key=api_key)
        
        # Enhanced system instruction to better handle various phrasings
        model = genai.GenerativeModel(
            model_name='gemini-1.5-flash-latest',
            tools=MAXIMO_TOOLS,
            system_instruction="""You are a helpful assistant that translates natural language requests into structured API calls for an IBM Maximo system. 
            
            Important guidelines:
            1. For asset updates, always use the 'update_asset' tool, not 'update_asset_status'
            2. When users ask to update multiple fields (like description, status, assettype), combine them into a single fields_to_update JSON string
            3. For asset creation, always require a siteid and use the 'create_asset' tool
            4. Field names should be lowercase in JSON (e.g., 'assettype' not 'ASSETTYPE')
            5. Always identify the correct tool based on the user's intent
            6. For listing assets in table format, use 'list_assets_table' tool
            
            You must only use the tools provided to you."""
        )
        
        print(f"--> Sending prompt to Gemini for function calling: '{user_prompt}'")
        response = model.generate_content(user_prompt)
        
        if response.candidates[0].content.parts[0].function_call:
            function_call = response.candidates[0].content.parts[0].function_call
            tool_name = function_call.name
            tool_args = {key: value for key, value in function_call.args.items()}
            print(f"--> Gemini identified tool: {tool_name} with args: {tool_args}")
            return {"status": "success", "tool_name": tool_name, "tool_args": tool_args}
        else:
            print("--> Gemini did not identify a tool. Returning text response.")
            return {"status": "text_response", "message": response.text}
    except Exception as e:
        print(f"An error occurred during tool call processing: {e}")
        return {"status": "error", "message": str(e)}
