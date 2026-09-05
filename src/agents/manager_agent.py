class ManagerAgent:
    def __init__(self):
        self.agents = {}

    def register_agent(self, name, agent):
        self.agents[name] = agent

    def diagnose(self, task):
        task = task.lower()

        if "email" in task or "gmail" in task:
            return "email_agent"

        if "telegram" in task:
            return "telegram_agent"

        if "sport" in task or "football" in task:
            return "sports_agent"

        return None

    def handle_task(self, task):
        agent_name = self.diagnose(task)

     