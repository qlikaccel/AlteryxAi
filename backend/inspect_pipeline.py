from app.services.alteryx_migration_engine import generate_python_project 
workflow={'name':'TestWorkflow','dataSources':[],'outputTargets':[],'workflowNodes':[]} 
project=generate_python_project(workflow) 
with open('temp_pipeline.py','w',encoding='utf-8') as f: 
    f.write(project['files']['pipeline.py']) 
print('wrote temp_pipeline.py') 
