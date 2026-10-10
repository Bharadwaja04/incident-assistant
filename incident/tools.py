"""OpenAI-format tool definitions generated from the schema, so prompt, tools and validator never drift apart.
Run:  python -m incident.tools   -> writes config/tools.json (the app / llama-server reads the same file)."""
import json, os
from .schema import CFG, FIELDS, PROPS, REQUIRED


def build_tools():
    fn = lambda name, desc, params: {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}
    return [
        fn("create_incident_report", "Call when ALL mandatory fields (incident_type, location, datetime, description) are known.",
           {"type": "object", "properties": PROPS, "required": REQUIRED}),
        fn("request_missing_info", "Call when a mandatory field is still missing. Put every field you extracted in partial_report and name the missing mandatory fields. Do not write a question.",
           {"type": "object", "properties": {
               "partial_report": {"type": "object", "properties": PROPS},
               "missing_fields": {"type": "array", "items": {"type": "string", "enum": REQUIRED}}},
            "required": ["partial_report", "missing_fields"]}),
        fn("update_report_field", "Call when the person corrects something while reviewing (e.g. 'make it high severity').",
           {"type": "object", "properties": {"field": {"type": "string", "enum": FIELDS},
                                             "value": {"type": ["string", "array"], "items": {"type": "string"}}},
            "required": ["field", "value"]}),
        fn("submit_report", "Call when the person confirms the report is correct.", {"type": "object", "properties": {}}),
    ]


if __name__ == "__main__":
    p = os.path.join(CFG, "tools.json")
    json.dump(build_tools(), open(p, "w"), indent=1)
    print("wrote", p)
