from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PaymentPlan:
    key: str
    title: str
    days: int
    price_toman: float


@dataclass
class PaymentRequestResult:
    redirect_url: str | None
    reference_code: str
    success: bool
    message: str = ""


class PaymentProvider(ABC):
    """Every payment backend (manual, Zarinpal, IDPay, ...) implements this
    interface. The web panel and bot only ever talk to this abstraction, so
    swapping "manual" for a real gateway later means writing one new class
    and changing PAYMENT_PROVIDER in the environment - nothing else.
    """

    name: str

    @abstractmethod
    def start_payment(self, user_id: int, plan: PaymentPlan) -> PaymentRequestResult:
        """Begin a payment. For the manual provider this just records intent;
        for a real gateway this would return a redirect_url to the bank page.
        """

    @abstractmethod
    def verify_payment(self, reference_code: str) -> bool:
        """Confirm a payment actually succeeded (called from a webhook / callback
        route for real gateways; always True for the manual provider).
        """
