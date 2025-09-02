import os
import sys
import argparse
import google.generativeai as genai

def build_prompt(scenario: str) -> str:
    """
    Constructs the detailed prompt for the Gemini API.
    """
    return f"""
You are an expert IBM Maximo Test Case writer. Your task is to convert a user-provided scenario into a detailed, formal test case formatted in Markdown.

**Instructions:**
1.  Analyze the user's scenario.
2.  Generate a comprehensive test case with the following sections:
    - **Test Case ID:** A unique identifier (e.g., TC-MAX-001).
    - **Title:** A concise and descriptive title based on the scenario.
    - **Objective:** A brief summary of what this test case aims to verify.
    - **Scenario:** The user-provided scenario.
    - **Prerequisites:** A list of all necessary preconditions, such as:
        - User Roles/Permissions (e.g., Maintenance Supervisor, Storeroom Clerk).
        - Required Data (e.g., An approved Work Order with status 'APPR', a specific item in the storeroom).
        - System State (e.g., User is logged into Maximo).
    - **Test Steps:** A numbered table with three columns: 'Step', 'Action', and 'Expected Result'. The steps must be clear, concise, and logical.
    - **Test Data:** A section listing any specific data used in the test, like Work Order numbers, Asset numbers, or User IDs.

**Example Output Format:**

# Test Case: Create and Approve a Corrective Maintenance Work Order

- **Test Case ID:** TC-MAX-001
- **Title:** Create and Approve a Corrective Maintenance Work Order
- **Objective:** To verify that a user with the appropriate permissions can successfully create a corrective maintenance work order, add a task, and get it approved.
- **Priority:** High
- **Scenario:** A maintenance supervisor needs to create a work order for a broken pump, assign a task to inspect it, and then approve the work order for scheduling.
- **Prerequisites:**
    - User is logged into Maximo.
    - User has permissions for the Work Order Tracking application.
    - User role: `MAINT-SUPER`
    - Asset `PUMP-123` exists in the system.
- **Test Steps:**
    | Step | Action                                                                | Expected Result                                                              |
    |------|-----------------------------------------------------------------------|------------------------------------------------------------------------------|
    | 1    | Navigate to the Work Order Tracking application.                      | The Work Order Tracking application opens.                                   |
    | 2    | Click the 'New Work Order' icon.                                      | A new work order record is created with a status of 'WAPPR'.                 |
    | 3    | In the 'Asset' field, enter `PUMP-123`.                               | The asset details populate correctly.                                        |
    | 4    | In the 'Description' field, enter "Pump is making a loud noise".      | The text is entered successfully.                                            |
    | 5    | Go to the 'Plans' tab and add a new task with description "Inspect pump". | The task is added to the work order.                                         |
    | 6    | From the 'Select Action' menu, choose 'Change Status'.                | The 'Change Status' dialog box appears.                                      |
    | 7    | Set the new status to 'APPR' and click OK.                            | The work order status changes to 'APPR' and the record becomes read-only.    |
- **Test Data:**
    | Data Field     | Value         |
    |----------------|---------------|
    | Asset Number   | `PUMP-123`    |
    | User ID        | `MAINT-SUPER` |

---
**User's Scenario to process:**
"{scenario}"
"""

def generate_maximo_test_case(scenario: str, api_key: str) -> str:
    """
    Uses the Gemini API to generate a Maximo test case from a scenario.

    Args:
        scenario: The user-provided scenario string.
        api_key: The Google API key.

    Returns:
        The generated test case in Markdown format.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        
        prompt = build_prompt(scenario)
        
        print("Generating test case with Gemini API...")
        response = model.generate_content(prompt)
        
        # Clean up the response to remove potential backticks from the model's output
        return response.text.strip().strip('```markdown').strip('```').strip()

    except Exception as e:
        print(f"An error occurred while communicating with the Gemini API: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    """
    Main function to parse arguments, generate the test case, and save it.
    """
    parser = argparse.ArgumentParser(
        description="Generate an IBM Maximo test case in Markdown format using the Gemini API.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "scenario",
        type=str,
        help="The test scenario to be converted into a test case. Enclose in quotes."
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="maximo_test_case.md",
        help="The name of the output Markdown file. (default: maximo_test_case.md)"
    )
    
    args = parser.parse_args()
    
    # --- API Key ---
    # WARNING: Storing API keys directly in code is not recommended for production
    # or shared environments. For simplicity, it is defined here.
    #
    # It is recommended to load the API key from an environment variable.
    API_KEY = "YOUR_API_KEY_HERE" # <-- PASTE YOUR KEY HERE
    
    if "YOUR_API_KEY_HERE" in API_KEY or not API_KEY:
        print("Error: Please open the script and replace 'YOUR_API_KEY_HERE' with your actual Google API key.", file=sys.stderr)
        sys.exit(1)

    test_case_markdown = generate_maximo_test_case(args.scenario, API_KEY)
    
    try:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(test_case_markdown)
        print(f"\nSuccessfully generated and saved test case to '{os.path.abspath(args.output)}'")
    except IOError as e:
        print(f"Error writing to file '{args.output}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()