from extraction import extract_mcap_data
result = extract_mcap_data('uploads/355- 2ce06427-453e-a4b2-077f-f3f05f23ce4f.mcap')
for phase in result.get('event_table', []):
    print(f"{phase['phase']}: HMI={phase['hmi_state']}, ODD={phase['odd_triggered']}, Duration={phase['duration_seconds']}s")

summary = result.get('analysis_summary', {})
for key in summary:
    print(key, ':', summary[key])

from database import get_db
conn = get_db()
draft = conn.execute("SELECT summary_data FROM report_drafts WHERE draft_id = (SELECT MAX(draft_id) FROM report_drafts)").fetchone()
print("Summary data:", draft["summary_data"])
conn.close()

from extraction import extract_mcap_data
result2 = extract_mcap_data('uploads/10056b8c-46ac-b9a4-b3cc-f1f0a3f55135.mcap')
print('Max Braking MCAP2:', result2['max_braking'])
print('Max Braking Raw:', result2['max_braking_raw'])
print('Event table:')
for phase in result2.get('event_table', []):
    print(f"  {phase['phase']}: {phase['timestamp']} duration={phase['duration_seconds']}s")