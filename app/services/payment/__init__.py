from app.config import settings
from app.services.payment.base import PaymentPlan, PaymentProvider, PaymentRequestResult
from app.services.payment.manual import ManualPaymentProvider

# Example plans - edit freely, or load from DB/config later.
PLANS: list[PaymentPlan] = [
    PaymentPlan(key="week", title="یک هفته", days=7, price_toman=0),
    PaymentPlan(key="month", title="یک ماه", days=30, price_toman=0),
]


def get_payment_provider() -> PaymentProvider:
    if settings.payment_provider == "manual":
        return ManualPaymentProvider()

    # To add a real gateway:
    #   elif settings.payment_provider == "zarinpal":
    #       from app.services.payment.zarinpal import ZarinpalPaymentProvider
    #       return ZarinpalPaymentProvider()
    raise ValueError(f"Unknown payment provider: {settings.payment_provider}")


__all__ = ["PaymentPlan", "PaymentProvider", "PaymentRequestResult", "PLANS", "get_payment_provider"]
