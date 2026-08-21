import uuid

from app.services.payment.base import PaymentPlan, PaymentProvider, PaymentRequestResult


class ManualPaymentProvider(PaymentProvider):
    """No real money moves through this provider. The admin looks at a bank
    transfer (e.g. card-to-card, common for small Iranian services) outside
    the system and then clicks "activate" in the admin panel, which calls
    services.subscription.activate_subscription directly.

    This class exists mainly so the rest of the codebase (bot commands,
    web routes) can be written once against the PaymentProvider interface
    and never need to change when a real gateway is added later.
    """

    name = "manual"

    def start_payment(self, user_id: int, plan: PaymentPlan) -> PaymentRequestResult:
        reference_code = f"manual-{user_id}-{uuid.uuid4().hex[:8]}"
        return PaymentRequestResult(
            redirect_url=None,
            reference_code=reference_code,
            success=True,
            message="Manual payment recorded, awaiting admin confirmation.",
        )

    def verify_payment(self, reference_code: str) -> bool:
        # Manual payments are confirmed by an admin action, not a callback.
        return True
