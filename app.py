import os
import json
import io
import zipfile
from flask import Flask, render_template, request, jsonify, send_file, flash, session, redirect, url_for
import requests # For handling exceptions from the requests library
from maximo_api_agent import MaximoAPIClient
from werkzeug.utils import secure_filename
import google.generativeai as genai

import maximo_natural_language_agent as maximo_nl_agent
# Import the functions from your existing script
import maximo_test_case_generator as generator

# --- Configuration ---
project_dir = r'C:\Users\2166337\Test_Case'
learning_dir = os.path.join(project_dir, 'Learning_Maximo')
index_dir = os.path.join(project_dir, 'Maximo_VectorIndex')
sample_test_dir = os.path.join(project_dir, 'sample_test')
output_dir = os.path.join(project_dir, 'output') # For final generated files

os.makedirs(learning_dir, exist_ok=True)
os.makedirs(index_dir, exist_ok=True)
os.makedirs(sample_test_dir, exist_ok=True)
os.makedirs(output_dir, exist_ok=True)

# By default, Flask automatically looks for templates in a folder named 'templates'.
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24) # Secret key is required for sessions


@app.route('/')
def index():
    """Renders the main home/landing page."""
    return render_template('home.html')

@app.route('/kb') # Corrected route from previous step
def knowledge_base():
    """Renders the knowledge base management page."""
    return render_template('kb_management.html')

@app.route('/agent')
def agent():
    """Renders the main test case agent page, loading from session if available."""
    current_test_case = session.get('current_test_case', None)
    return render_template('index.html', current_test_case=current_test_case)

@app.route('/new_agent_session')
def new_agent_session():
    """Clears the current test case from the session and redirects to the agent page."""
    session.pop('current_test_case', None)
    flash("Previous session cleared. You can start a new test case.", "message")
    return redirect(url_for('agent'))

@app.route('/process_chat_message', methods=['POST'])
def process_chat_message():
    """
    Receives a user message, classifies its intent, and routes to the
    appropriate generation or modification logic.
    """
    try:
        # Get data from the form
        scenario_or_instruction = request.form.get('scenario')
        model_name = request.form.get('model_name', 'gemini-1.5-flash-latest')
        google_api_key = request.form.get('google_api_key')

        if not scenario_or_instruction:
            return jsonify({'error': 'Input cannot be empty.'}), 400
        if not google_api_key:
            return jsonify({'error': 'Google API Key is required.'}), 400

        # API keys are now passed per-request
        api_keys = {"google": google_api_key}
        # Step 1: Classify the user's intent
        intent = generator.classify_user_intent(scenario_or_instruction, api_keys)
        print(f"--> Classified intent as: {intent}")

        if intent == "GENERATE":
            # This is a new scenario, so clear any previous session data.
            session.pop('current_test_case', None)
            
            template_stream = None
            if 'template_file' in request.files and request.files['template_file'].filename != '':
                template_stream = request.files['template_file']

            example_section = generator._create_example_section(template_stream=template_stream)
            custom_context = generator.retrieve_relevant_context(scenario_or_instruction, index_dir, google_api_key)

            markdown_result = generator.generate_maximo_test_case(
                scenario_or_instruction, api_keys, custom_context, example_section, model_name
            )

            parsed_data = generator.parse_markdown_to_dict(markdown_result)
            parsed_data['scenario'] = scenario_or_instruction # Store original scenario
            
            session['current_test_case'] = parsed_data
            return jsonify(parsed_data)

        elif intent == "MODIFY":
            current_test_case = session.get('current_test_case')
            if not current_test_case or 'test_steps' not in current_test_case:
                return jsonify({'error': 'There is no active test case to modify. Please generate one first.'}), 400

            current_steps = current_test_case['test_steps']
            
            import pandas as pd
            df = pd.DataFrame(current_steps)
            steps_table_md = df.to_markdown(index=False)

            updated_table_md = generator.modify_test_steps(steps_table_md, scenario_or_instruction, api_keys, model_name)

            lines = updated_table_md.strip().split('\n')
            header = [h.strip() for h in lines[0].strip('|').split('|')]
            updated_steps = []
            for line in lines[2:]:
                parts = [p.strip() for p in line.strip('|').split('|')]
                if len(parts) == len(header):
                    updated_steps.append(dict(zip(header, parts)))
            
            session['current_test_case']['test_steps'] = updated_steps
            session.modified = True
            
            return jsonify(session['current_test_case'])

    except Exception as e:
        print(f"An unexpected error occurred in process_chat_message: {e}")
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/finalize', methods=['POST'])
def finalize():
    """Handles the final file generation and download."""
    try:
        # Get the definitive test case data from the session, not the client.
        # This is more secure and reliable.
        final_data = session.get('current_test_case')
        
        if not final_data:
            return jsonify({'error': 'No active test case session found to finalize.'}), 400

        # Reconstruct the full markdown on the server for reliability.
        full_markdown = f"# Test Case: {final_data.get('title', '')}\n\n"
        full_markdown += f"- **Test Case ID:** {final_data.get('test_case_id', '')}\n"
        full_markdown += f"- **Title:** {final_data.get('title', '')}\n"
        full_markdown += f"- **Objective:** {final_data.get('objective', '')}\n"
        full_markdown += f"- **Scenario:** {final_data.get('scenario', '')}\n"
        full_markdown += f"- **Prerequisites:** {final_data.get('prerequisites', '')}\n\n"
        full_markdown += f"- **Test Steps:**\n"
        full_markdown += f"| Actions | Expected Result | Actual Result |\n"
        full_markdown += f"|---|---|---|\n"
        if final_data.get('test_steps'):
            for step in final_data['test_steps']:
                full_markdown += f"| {step.get('Actions', '')} | {step.get('Expected Result', '')} | {step.get('Actual Result', '')} |\n"


        # Create the output files in memory
        md_filename = "final_test_case.md"
        xlsx_filename = "final_test_case_steps.xlsx"
        
        # Use BytesIO to keep files in memory instead of writing to disk on the server
        md_io = io.BytesIO(full_markdown.encode('utf-8'))
        md_io.seek(0)

        # Create a temporary path for the excel file to be saved by the generator function
        temp_xlsx_path = os.path.join(output_dir, xlsx_filename)
        generator.save_steps_to_excel(full_markdown, temp_xlsx_path)

        # Create a zip file in memory
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(md_filename, md_io.read())
            zf.write(temp_xlsx_path, arcname=xlsx_filename)
        zip_io.seek(0)

        # Clean up the temp excel file
        os.remove(temp_xlsx_path)

        return send_file(
            zip_io,
            mimetype='application/zip',
            as_attachment=True,
            download_name='Maximo_TestCase_Package.zip'
        )

    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred during finalization: {str(e)}'}), 500


@app.route('/update_kb', methods=['POST'])
def update_kb():
    """Handles uploading a new document to the knowledge base and updating the index."""
    try:
        if 'kb_file' not in request.files or request.files['kb_file'].filename == '':
            flash('No file selected for knowledge base update.')
            return jsonify({'error': 'No file selected.'}), 400

        kb_file = request.files['kb_file']
        filename = secure_filename(kb_file.filename)
        
        # Save the new document to the Learning_Maximo folder
        kb_file.save(os.path.join(learning_dir, filename))
        
        # Immediately trigger the index update
        # This can take time, so for a real production app, you'd use a background task queue.
        # For this script, we'll run it directly and the user will wait.
        generator.update_vector_index(learning_dir, index_dir, os.environ.get("GOOGLE_API_KEY")) # Admin function still uses env var

        return jsonify({'message': f"Knowledge base successfully updated with '{filename}'. The index has been rebuilt."})

    except Exception as e:
        return jsonify({'error': f'An error occurred while updating the knowledge base: {str(e)}'}), 500


# --- Maximo Agent Routes ---

def get_maximo_client(host, api_key):
    """Helper function to instantiate and return a Maximo client or raise an error."""
    if not host or not api_key:
        raise ValueError("Maximo Host and Maximo API Key must be provided.")
    return MaximoAPIClient(host=host, api_key=api_key)

@app.route('/maximo_chat_agent')
def maximo_chat_agent():
    """Renders the new conversational Maximo agent page."""
    return render_template('maximo_chat_agent.html')

@app.route('/maximo/process_chat', methods=['POST'])
def maximo_process_chat():
    """
    Processes a natural language query for the Maximo agent,
    determines the correct API call, executes it, and returns the result.
    """
    try:
        data = request.get_json()
        user_prompt = data.get('prompt')
        google_api_key = data.get('google_api_key')
        maximo_host = data.get('maximo_host')
        maximo_api_key = data.get('maximo_api_key')

        if not user_prompt:
            return jsonify({"status": "error", "message": "Prompt cannot be empty."}), 400
        if not all([google_api_key, maximo_host, maximo_api_key]):
            return jsonify({"status": "error", "message": "Maximo Host, Maximo API Key, and Google API Key are all required."}), 400

        tool_call_result = maximo_nl_agent.get_maximo_tool_call(user_prompt, google_api_key)

        if tool_call_result.get('status') != 'success':
            return jsonify(tool_call_result)

        tool_name = tool_call_result.get('tool_name')
        tool_args = tool_call_result.get('tool_args', {})

        client = get_maximo_client(host=maximo_host, api_key=maximo_api_key)
        
        # Dynamically call the method on the client instance
        if hasattr(client, tool_name):
            method_to_call = getattr(client, tool_name)
            result = method_to_call(**tool_args)
        else:
            return jsonify({"status": "error", "message": f"Unknown tool identified: {tool_name}"}), 400

        if result is not None: # This means no network/API error occurred
            if result: # Asset(s) were found and result is a non-empty list
                return jsonify({"status": "success", "data": result, "tool_called": tool_name})
            else: # No assets were found (result is an empty list)
                assetnum = tool_args.get('assetnum', '')
                siteid = tool_args.get('siteid', 'any')
                return jsonify({"status": "not_found", "message": f"No assets found matching asset number(s) '{assetnum}' at site '{siteid}'."})
        else: # An error occurred (result is None)
            return jsonify({"status": "error", "message": f"The action '{tool_name}' failed. Check server logs for connection or permission issues."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    print("Flask server starting...")
    print(f"Open your browser and go to http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)