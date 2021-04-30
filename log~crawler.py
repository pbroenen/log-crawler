import argparse
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

# Read the JSON config file into memory.

session = {}
session['widgets'] = {}
with open(r'log_crawler_config.json') as f:
    config = json.load(f)
    debug = config['debug']

# Validate command line arguments.

parser = argparse.ArgumentParser(description=config['argparse']['description'])
for arg in config['argparse']['args']:
    parser.add_argument(arg['name'], help=arg['help'])
args = parser.parse_args()

# Normalize the session log file path.

session['log_file'] = (os.path.dirname(args.log_file) or config['log_dir'].format(e=os.environ)) + '/' + os.path.basename(args.log_file)

# Convert binary file to XML.

print config['prompt']['running_xml']
infacmd = []
for arg in config['infacmd']:
    infacmd.append(arg.format(e=os.environ, s=session))
subprocess.call(infacmd)

# Convert XML to Python dict.

xml = ET.parse(session['log_file'] + '.xml')
log = xml.getroot()

# Establish a bi-directional connection with the Informatica repository.

sqlplus = []
for arg in config['sqlplus']:
    sqlplus.append(arg.format(e=os.environ))

# Define functions.

def query_repo(raw_sql, session, widget):
    # Format SQL and execute it on the Informatica repository database.
    infa_repo = subprocess.Popen(sqlplus, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sql = '\n'.join(raw_sql).format(s=session, w=widget)
    result = infa_repo.communicate(input=b'SET HEADING OFF\n' + sql + '\n')[0].split()
    return result[0]

def to_bytes(string):
    # Convert strings like '2GB' to bytes.
    result = int(re.sub('[^0-9]', '', string))
    for k, power in config['k_power']:
        x = string.find(k)
        if x > -1:
            result = result * (1024 ** power)
            break
    return result

#
# Step through the XML log file contents, line by line.
#
for logEvent in log:
    thread_name = logEvent.attrib['threadName'] or 'UNKNOWN'
    message_code = logEvent.attrib['messageCode'] or 'UNKNOWN'
    message = logEvent.attrib['message'] or 'UNKNOWN'
    finds = re.findall('\[(.*?)\]', message)

    if message_code in ['TM_6014', 'TM_6187', 'TM_6683', 'TM_6685', 'TM_6686']:
        # Repository name, Subject name, Workflow named, Precision, etc.
        for i in range(len(config['message_codes'][message_code]['args'])):
            session[config['message_codes'][message_code]['args'][i]] = finds[i]

    elif message_code == 'TM_6101':
        # Mapping name.
        finds = message.split()
        session['mapping_name'] = finds[2] or 'UNKNOWN'

    elif message_code == 'TM_6156':
        # Precision.
        finds = message.split()
        session['precision'] = finds[1]

    elif message_code == 'TM_6660' and thread_name == 'MAPPING':
        # DTM and Block sizes.
        finds = re.findall(r'\b\d+\b', message)
        session['log_dtm_buffer_bytes'] = int(finds[0]) or 0
        session['log_buffer_block_bytes'] = int(finds[1]) or 0

    elif message_code in ['CMN_1790', 'CMN_1791', 'CMN_1792', 'CMN_1793', 'CMN_1794', 'CMN_1795', 'SORT_40419']:
        # Cache sizes for Aggregators, Lookups, Joiners, and Sorters.
        w = {}
        for i in range(len(config['message_codes'][message_code]['args'])):
            w[config['message_codes'][message_code]['args'][i]] = finds[i]

        if w['widget_instance_name'] in session['widgets']:
            session['widgets'][w['widget_instance_name']].update(w)
        else:
            session['widgets'][w['widget_instance_name']] = w


# Session log details have been parsed into memory.
# Retrieve session details from Informatica repository.

for key, tuple in sorted(config['session']['attributes'].items()):
    session[tuple['name']] = query_repo(tuple['sql']['select'] + tuple['sql']['where'], session, widget={})

# Loop through the session widgets to find their current repo settings.

for widget_instance_name in session['widgets']:
    for key, tuple in sorted(config['widget']['attributes'].items()):
        session['widgets'][widget_instance_name][tuple['name']] = query_repo(tuple['sql']['select'] + tuple['sql']['where'], session, session['widgets'][widget_instance_name])

    for key, tuple in sorted(config['widget']['types'][session['widgets'][widget_instance_name]['widget_type_id']]['attributes'].items()):
        session['widgets'][widget_instance_name][tuple['name']] = query_repo(tuple['sql']['select'] + tuple['sql']['where'], session, session['widgets'][widget_instance_name])

# Analyze the details.

print "\nRepository Name:", session['repository_name']
print "Folder Name:", session['subject_name']
print "Workflow Name:", session['workflow_name']
#print "Workflow ID:", session['workflow_id']
#print "Workflow Version Number:", session['workflow_version_number']
print "Session Instance Name:", session['session_instance_name']
#print "Session Instance ID:", session['session_instance_id']
#print "Session ID:", session['session_id']
#print "Session Version Number:", session['session_version_number']
print "Precision:", session['precision']

#
# Both DTM Buffer Size and Default Buffer Block Size should be set to Auto.
#
for attr_key, attr_tuple in sorted(config['session']['attributes'].items()):
    if attr_tuple['name'] in ('repo_dtm_buffer_size', 'repo_buffer_block_size'):
        print config['prompt']['title'].format(attr_tuple['title'])
        print config['prompt']['in_repo'].format(session[attr_tuple['name']])

        if session[attr_tuple['name']] == 'Auto':
            print config['prompt']['no_tuning']
        elif attr_tuple['source'] in session:
            print config['prompt']['in_log'].format(session[attr_tuple['source']])
            if to_bytes(session[attr_tuple['name']]) * session[attr_tuple['threshold']] < int(session[attr_tuple['source']]):
                print config['prompt']['updating'].format(attr_tuple['title'], session[attr_tuple['name']], session[attr_tuple['source']])
                result = query_repo(attr_tuple['sql']['update'] + attr_tuple['sql']['where'], session, widget={})
                print result
        else:
            print config['prompt']['no_recommendation']

#
# Check whether the log recommended larger cache sizes for any if the widgets.
#
for widget_key, widget_tuple in sorted(session['widgets'].items()):
    print "\n" + widget_key
    for attr_key, attr_tuple in sorted(config['widget']['types'][widget_tuple['widget_type_id']]['attributes'].items()):
        print config['prompt']['title'].format(attr_tuple['title'])
        print config['prompt']['in_repo'].format(widget_tuple[attr_tuple['name']])

        if attr_tuple['source'] in widget_tuple:
            print config['prompt']['in_log'].format(widget_tuple[attr_tuple['source']])
            if int(widget_tuple[attr_tuple['source']]) == 0:
                print config['prompt']['no_recommendation']
            elif widget_tuple[attr_tuple['name']] == 'no' or to_bytes(widget_tuple[attr_tuple['name']]) < int(widget_tuple[attr_tuple['source']]):
                print config['prompt']['updating'].format(attr_tuple['title'], widget_tuple[attr_tuple['name']], widget_tuple[attr_tuple['source']])
                result = query_repo(attr_tuple['sql']['update'] + attr_tuple['sql']['where'], session, widget_tuple)
                print config['prompt']['update_success' if result == 1 else 'update_failure'].format(result)
            else:
                print config['prompt']['no_tuning']
        else:
            print config['prompt']['no_recommendation']

# EOF
