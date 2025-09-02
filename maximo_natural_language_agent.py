import google.generativeai as genai
import json

# This is the description of the tools that the LLM can use.
# It's based on the methods in maximo_api_agent.py. This structured format
# allows the AI to understand what functions are available and what arguments they need.
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
                    "description": "A comma-separated list of fields to retrieve, e.g., 'description,status,serialnum,installdate'."
                }
            },
            "required": ["assetnum"]
        }
    },
    {
        "name": "update_asset_status",
        "description": "Updates the status of an existing asset in Maximo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assetnum": {
                    "type": "STRING",
                    "description": "The unique identifier for the asset to be updated."
                },
                "new_status": {
                    "type": "STRING",
                    "description": "The new status to set for the asset, e.g., 'ACTIVE', 'DECOMMISSIONED'."
                },
                "siteid": {
                    "type": "STRING",
                    "description": "The site identifier for the asset."
                }
            },
            "required": ["assetnum", "new_status"]
        }
    },
    {
        "name": "test_connection",
        "description": "Tests the connection and authentication to the Maximo server. Does not require any parameters.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    }
]

def get_maximo_tool_call(user_prompt: str, api_key: str):
    """
    Uses the Gemini API with function calling to determine which Maximo tool to use.
    """
    try:
        genai.configure(api_key=api_key)
        # Adding a system instruction gives the model a clearer role and improves reliability.
        model = genai.GenerativeModel(
            model_name='gemini-1.5-flash-latest',
            tools=MAXIMO_TOOLS,
            system_instruction="You are a helpful assistant that translates natural language requests into structured API calls for an IBM Maximo system. You must only use the tools provided to you."
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