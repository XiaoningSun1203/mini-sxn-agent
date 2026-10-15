
class ApprovalController:
    # get user response
    def query_user_approval(self) -> bool:
        response = input(f"[Press 'y' to continue, or 'n' to stop ...").strip().lower()
        return response in {'y', 'yes'}
