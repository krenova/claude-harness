import asyncio
import subprocess
import json
import re
import os
import glob
import logging

PATH_AMA_PLANS = "./ama_plans"
PATH_AMA_ARTIFACTS = "./ama_artifacts"
PATH_LOGS = "./ama_logs"

os.makedirs(PATH_AMA_PLANS, exist_ok=True) # Create plans directory if it doesn't exist
os.makedirs(PATH_AMA_ARTIFACTS, exist_ok=True) # Create artifacts directory if it doesn't exist
os.makedirs(PATH_LOGS, exist_ok=True) # Create logs directory if it doesn't exist

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(module)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=f'{PATH_LOGS}/orchestrator.log', # Saves logs to this file
    filemode='a'                 # 'a' appends to the file, 'w' overwrites it each run
)
# Also print logs to the console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(module)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.getLogger().addHandler(console_handler)

# ==========================================
# CONFIGURATION
# ==========================================
X1_MAX_WORKERS = 3  # Maximum number of independent agents running at once
N_MAX_LOOPS = 5     # Maximum execution loops per phase before forcing a halt
MAX_TURNS = "15"    # Max autonomous tool loops Claude can take per session

# ==========================================
# CORE AGENT RUNNERS
# ==========================================

async def run_worker_agent(sem, worker_id, task_prompt):
    """Runs an independent worker agent asynchronously, bounded by a Semaphore."""
    async with sem:
        logging.info(f"  🚀 [WORKER {worker_id}] Starting task...")
        full_prompt = f"""
        You are Independent Worker {worker_id}. 
        Execute the following task using your tools. Do not ask for human input. If you are not able to execute the task, do your best to get as much done as possible and explain what you couldn't complete and why.
        When finished, write a brief summary of your findings/actions to a file named '{PATH_AMA_ARTIFACTS}/worker_{worker_id}_output.md' and exit.
        TASK: {task_prompt}
        """
        
        process = await asyncio.create_subprocess_exec(
            "claude", "-p", full_prompt, "--dangerously-skip-permissions", "--max-turns", MAX_TURNS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        
        # Read the output file the worker created
        output_file = f"{PATH_AMA_ARTIFACTS}/worker_{worker_id}_output.md"
        result = "Worker completed, but no output file was found."
        if os.path.exists(output_file):
            with open(output_file, "r") as f:
                result = f.read()
            os.remove(output_file) # Clean up
            
        logging.info(f"  ✅[WORKER {worker_id}] Finished.")
        return f"--- WORKER {worker_id} REPORT ---\n{result}\n"

def run_orchestrator(prompt, require_json=False):
    """Runs the Master Orchestrator synchronously."""
    logging.info(f"\n🧠 [ORCHESTRATOR] Thinking...")
    
    if require_json:
        prompt += "\n\nIMPORTANT: You MUST output your response ONLY as a valid JSON block enclosed in ```json ... ``` tags. Do not include conversational text outside the JSON block."

    process = subprocess.Popen(["claude", "-p", prompt, "--dangerously-skip-permissions", "--max-turns", MAX_TURNS], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate()
    
    if require_json:
        match = re.search(r'```json\s*(.*?)\s*```', stdout, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                logging.error("❌ Failed to parse Orchestrator JSON.")
                return None
        return None
        
    return stdout

# ==========================================
# PHASE 0: PLAN REVIEW & REFINEMENT
# ==========================================

async def plan_refinement_phase():
    logging.info("\n" + "="*50)
    logging.info("🎯 PHASE 0: MULTI-PHASED PLAN REVIEW")
    logging.info("="*50)
    
    if not os.path.exists(f"{PATH_AMA_PLANS}/initial_plan.md"):
        logging.error("❌ Error: 'initial_plan.md' not found. Please create it first.")
        exit(1)

    while True:
        # 1. Orchestrator reviews the plan and decides if research is needed
        delegation_prompt = f"""
        Read 'initial_plan.md'. We need to refine this into a robust multi-phased plan.
        Crucially, every phase MUST have a set of KPIs (unit/integration tests, or specific verifiable tasks if tests aren't possible) to ensure functionality and integration with adjacent phases.
        
        Do you need independent agents to conduct research (e.g., checking library docs, exploring API limits, checking feasibility) before finalizing the plan?
        Output a JSON array of research tasks. If no research is needed, output an empty array[].
        Format: {{"research_tasks": ["task 1", "task 2"]}}
        """
        delegation_data = run_orchestrator(delegation_prompt, require_json=True)
        
        # 2. Spin up workers if research is needed
        if delegation_data and delegation_data.get("research_tasks"):
            tasks = delegation_data["research_tasks"]
            logging.info(f"\n🔍 Orchestrator delegated {len(tasks)} research tasks to workers.")
            sem = asyncio.Semaphore(X1_MAX_WORKERS)
            worker_coroutines =[run_worker_agent(sem, i+1, task) for i, task in enumerate(tasks)]
            research_results = await asyncio.gather(*worker_coroutines)
            
            research_context = "\n".join(research_results)
        else:
            research_context = "No additional research was required."

        # 3. Orchestrator finalizes the plan and splits it into files
        split_plan_prompt = f"""
        Here is the research gathered by the workers:
        {research_context}
        
        Based on 'initial_plan.md' and the research, create the finalized multi-phased plan.
        1. Break the plan down into separate files named exactly '{PATH_AMA_PLANS}/phase_1_plan.md', '{PATH_AMA_PLANS}/phase_2_plan.md', etc.
        2. In each file, explicitly list the KPIs (tests or verifiable tasks) required to complete the phase.
        3. Write a summary of the overall architecture to '{PATH_AMA_PLANS}/architecture_summary.md'.
        Use your file writing tools to create these files now.
        """
        run_orchestrator(split_plan_prompt)
        
        # ---------------------------------------------------------
        # NEW STEP 3.5: ORCHESTRATOR SEEKS CLARIFICATIONS
        # ---------------------------------------------------------
        clarification_prompt = f"""
        You have just drafted the phase plans and architecture summary in the {PATH_AMA_PLANS} directory.
        Before we proceed to execution, act as a strict Senior Staff Engineer. 
        Review the plans you just created. Are there any missing links, ambiguous requirements, potential security flaws, or oversights?
        
        Write a brief 'Risk Assessment & Clarifications' report addressed to the human supervisor. 
        Explicitly list any questions you need the human to answer or clarify before we can safely proceed to coding.
        """
        clarification_report = run_orchestrator(clarification_prompt)
        
        logging.info("\n" + "="*50)
        logging.info("🧐 ORCHESTRATOR RISK ASSESSMENT & CLARIFICATIONS")
        logging.info("="*50)
        print(f"\n{clarification_report}\n") # Print directly to console so the human sees it immediately
        
        # 4. Human-In-The-Loop (HITL)
        logging.info("\n" + "="*50)
        logging.info("👨‍💻 HUMAN REVIEW REQUIRED")
        logging.info(f"The Orchestrator has generated the phase plan files in {PATH_AMA_PLANS}/.")
        
        user_input = input("Type 'approve' to begin execution, OR type your answers to the Orchestrator's questions/feedback: ")
        
        if user_input.lower() in ['approve', 'yes', 'y']:
            logging.info("✅ Plan approved. Moving to Execution Phase.")
            break
        else:
            logging.info("🔄 Sending human feedback back to Orchestrator to update plans...")
            fix_prompt = f"""
            The human supervisor provided the following answers and feedback to your questions: 
            '{user_input}'
            
            Based on this new information, use your file editing tools to update the relevant phase_X_plan.md and architecture_summary.md files in the {PATH_AMA_PLANS} directory.
            """
            run_orchestrator(fix_prompt)
            # The loop will now restart, allowing the Orchestrator to re-evaluate the updated plans.

# ==========================================
# PHASE 2: EXECUTION LOOPS
# ==========================================

async def execution_phase():
    logging.info("\n" + "="*50)
    logging.info("🚀 PHASE 2: PLAN EXECUTION")
    logging.info("="*50)
    
    # Find all phase files generated in Phase 1
    phase_files = sorted(glob.glob(f"{PATH_AMA_PLANS}/phase_*_plan.md"))
    if not phase_files:
        logging.error("❌ No phase plan files found!")
        return

    for phase_file in phase_files:
        phase_name = os.path.basename(phase_file).replace('_plan.md', '')
        memory_file = f"{PATH_AMA_ARTIFACTS}/{phase_name}_memory.md"
        
        # Initialize Memory File
        with open(memory_file, "w") as f:
            f.write(f"# Memory for {phase_name}\nExecution started.\n")
            
        logging.info(f"\n" + "-"*40)
        logging.info(f"⚙️ COMMENCING: {phase_name.upper()}")
        logging.info("-"*40)

        for loop_num in range(1, N_MAX_LOOPS + 1):
            logging.info(f"\n🔄 [LOOP {loop_num}/{N_MAX_LOOPS}] Planning execution...")
            
            # 1. Orchestrator plans tasks for workers
            task_prompt = f"""
            We are executing {phase_file}. 
            Read {phase_file} and {memory_file}. Look at the current codebase state.
            What tasks need to be executed right now by the worker agents to progress this phase and meet the KPIs?
            Output a JSON object containing a list of tasks. If the phase is completely finished and all KPIs are met, output an empty list.
            Format: {{"tasks":["write backend tests", "implement login UI"]}}
            """
            task_data = run_orchestrator(task_prompt, require_json=True)
            tasks = task_data.get("tasks", []) if task_data else[]
            
            # 2. Execute Tasks via Workers (Bounded by X1)
            if tasks:
                logging.info(f"🛠️ Orchestrator delegated {len(tasks)} execution tasks.")
                sem = asyncio.Semaphore(X1_MAX_WORKERS)
                worker_coroutines =[run_worker_agent(sem, i+1, task) for i, task in enumerate(tasks)]
                await asyncio.gather(*worker_coroutines)
            else:
                logging.info("No tasks delegated. Orchestrator believes phase might be complete.")

            # 3. Orchestrator Reviews Work against KPIs
            review_prompt = f"""
            The workers have finished their tasks. 
            Review the current codebase against the KPIs defined in {phase_file}.
            Run any necessary unit/integration tests using your bash tools.
            
            Are all KPIs met? Are there bugs? Do we need to add new KPIs based on new findings?
            Output a JSON object:
            {{
                "kpis_met": true/false,
                "summary": "Brief summary of what works and what is broken",
                "proposed_fixes_or_new_kpis": "What needs to happen next loop, if anything"
            }}
            """
            review_data = run_orchestrator(review_prompt, require_json=True)
            
            if not review_data:
                continue

            logging.info(f"\n📊 ORCHESTRATOR REVIEW:\n- KPIs Met: {review_data.get('kpis_met')}\n- Summary: {review_data.get('summary')}")
            
            # 4. Update Memory
            update_memory_prompt = f"Update {memory_file} with a summary of Loop {loop_num}: what was done, what failed, and current KPI status."
            run_orchestrator(update_memory_prompt)

            # 5. HITL / Convergence Check
            if review_data.get("kpis_met"):
                logging.info(f"\n✅ {phase_name.upper()} KPIs met!")
                break
            else:
                logging.info(f"\n⚠️ KPIs not met. Proposed fixes: {review_data.get('proposed_fixes_or_new_kpis')}")
                user_input = input("👨‍💻 HUMAN INPUT: Type 'continue' to let the AMA fix this in the next loop, or provide specific guidance/new KPIs: ")
                
                if user_input.lower() not in ['continue', 'c', 'yes', 'y']:
                    # Inject human feedback into memory for the next loop
                    with open(memory_file, "a") as f:
                        f.write(f"\nHuman Feedback for next loop: {user_input}\n")
                        
        else:
            logging.info(f"\n⚠️ Reached maximum loops ({N_MAX_LOOPS}) for {phase_name}. Forcing progression.")

        # 6. Phase Completion Report
        report_prompt = f"""
        We have finished {phase_name}. 
        Read {memory_file} and the codebase. 
        Draft a comprehensive markdown report named '{phase_name}_report.md' summarizing the work completed, the KPIs achieved, and any technical debt left over.
        """
        run_orchestrator(report_prompt)
        logging.info(f"📝 Generated {phase_name}_report.md")

    logging.info("\n🎉 ALL PHASES COMPLETED SUCCESSFULLY! 🎉")

# ==========================================
# MAIN ENTRY POINT
# ==========================================
if __name__ == "__main__":
    async def main():
        await plan_refinement_phase()
        await execution_phase()
        
    # Run the asyncio event loop
    asyncio.run(main())