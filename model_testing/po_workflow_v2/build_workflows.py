"""Rebuild importable workflows from reviewed Code-node sources. No network calls."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
old = json.loads((ROOT.parent / 'n8n_po_project_workflow.json').read_text(encoding='utf-8'))
nodes, connections = [], {}

def node(name, kind, params, x, y=0, version=1, **extra):
    n = dict(id=name.lower().replace(' ', '-'), name=name, type='n8n-nodes-base.'+kind,
             typeVersion=version, position=[x,y], parameters=params, **extra)
    nodes.append(n)
    return n

def code(name, source, x, y=0):
    return node(name, 'code', {'mode':'runOnceForAllItems','jsCode':source}, x,y,2)

def source(name):
    return (ROOT / name).read_text(encoding='utf-8')

def edge(a,b,output=0):
    outputs = connections.setdefault(a, {'main':[]})['main']
    while len(outputs)<=output: outputs.append([])
    outputs[output].append({'node':b,'type':'main','index':0})

def condition(name, expression, value, x,y=0):
    p=copy.deepcopy(next(n['parameters'] for n in old['nodes'] if n['type']=='n8n-nodes-base.if'))
    p['conditions']['conditions'][0].update(leftValue=expression,rightValue=value)
    return node(name,'if',p,x,y,2)

node('Run Test','manualTrigger',{},0)
code('Run Settings',source('settings.js'),220)
http=copy.deepcopy(next(n for n in old['nodes'] if n['type']=='n8n-nodes-base.httpRequest'))
http.update(name='Call Current Modal Model',id='modal-model',position=[440,0])
http['parameters']['options']={'timeout':300000,'response':{'response':{'fullResponse':True,'responseFormat':'json'}}}
http['onError']='continueErrorOutput'
nodes.append(http)
code('Normalize and Validate',source('normalize_modal.js'),660)
condition('Valid Result?', '={{ $json.status }}','passed',880)
code('Build PO Review',source('build_review.js'),1100)
code('Download Full Results',source('export_results.js'),1320)
condition('Storage Enabled?', "={{ String($('Run Settings').first().json.storage_enabled) }}",'true',1540)
node('Save Test Run','executeWorkflow',{'source':'database','workflowId':{'__rl':True,'mode':'id','value':"={{ $('Run Settings').first().json.storage_workflow_id }}"},'mode':'once','options':{'waitForSubWorkflow':True}},1760,-120,1.2)
code('Confirm Storage',"const a=$input.first().json; const d=$('Download Full Results').first(); if(a.storage_status!=='stored'||a.run_id!==d.json.run_id) throw new Error('Storage failed or conflicting run ID; inspect storage output'); return [{json:{...d.json,storage_status:'stored'},binary:d.binary}];",1980,-120)
code('Storage Skipped',"const d=$input.first(); return [{json:{...d.json,storage_status:'not_configured'},binary:d.binary}];",1760,120)
code('Test Run Report',"const d=$input.first(); return [{json:{...d.json,report:{result:'TEST PASSED',storage:d.json.storage_status,total_skus:d.json.summary.total_skus,review_queue_count:d.json.summary.review_queue_count,po_decisions:d.json.summary.po_decisions,instruction:'Download the three files from Binary output. Review recommendations before any future live integration.'}},binary:d.binary}];",2200)
node('Stop Invalid Run','stopAndError',{'errorType':'errorMessage','errorMessage':"={{ 'PO test failed at ' + $json.stage + ': ' + $json.errors.join('; ') }}"},1100,260)
for a,b in [('Run Test','Run Settings'),('Run Settings','Call Current Modal Model'),('Call Current Modal Model','Normalize and Validate'),('Normalize and Validate','Valid Result?'),('Valid Result?','Build PO Review'),('Build PO Review','Download Full Results'),('Download Full Results','Storage Enabled?'),('Storage Enabled?','Save Test Run'),('Save Test Run','Confirm Storage'),('Confirm Storage','Test Run Report'),('Storage Skipped','Test Run Report')]: edge(a,b)
edge('Call Current Modal Model','Normalize and Validate',1)
edge('Valid Result?','Stop Invalid Run',1)
edge('Storage Enabled?','Storage Skipped',1)
node('Setup and Boundaries','stickyNote',{'content':'## PO workflow V2\nManual sample test → Modal HTTP → validation → complete PO review → CSV/JSON exports → optional PostgreSQL.\nEdit Run Settings. Storage is OFF by default.\nCurrent model/auth is copied from your existing workflow. Replace the HTTP node and adapter when the new model is ready.\nSmartRetail ingestion, live inventory, supplier submission, website and RAG remain future integrations.','width':760,'height':270},260,-360)

def write(name,title):
    (ROOT/name).write_text(json.dumps({'name':title,'nodes':nodes,'connections':connections,'active':False,
        'settings':{'executionOrder':'v1','timezone':'Australia/Melbourne'},'pinData':{}},indent=2),encoding='utf-8')

write('n8n_po_workflow_v2.json','Pearcedale PO - Full Model Test V2')
nodes,connections=[],{}
node('Receive Test Run','executeWorkflowTrigger',{'inputSource':'passthrough'},0,version=1.1)
code('Check Storage Input',"const d=$input.first().json; if(d.schema_version!=='po-test.v2'||d.status!=='passed'||d.inventory_is_simulated!==true||d.submission_allowed!==false||!Array.isArray(d.forecasts)||!Array.isArray(d.recommendations)||!/^[a-f0-9]{32}$/.test(d.run_id)) throw new Error('Invalid test storage input'); return [{json:d}];",220)
node('Store Run in PostgreSQL','postgres',{'operation':'executeQuery','query':source('persist.sql'),'options':{'queryReplacement':'={{ [JSON.stringify($json)] }}'}},440,version=2.6)
edge('Receive Test Run','Check Storage Input');edge('Check Storage Input','Store Run in PostgreSQL')
write('n8n_po_storage_v2.json','Pearcedale PO - Optional Test Storage V2')
print('Generated main workflow and optional storage workflow')
