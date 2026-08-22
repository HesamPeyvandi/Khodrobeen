import asyncio
from app.services.divar_client import DivarScraper

async def main():
    async with DivarScraper() as scraper:
        listings = await scraper.list_new_listings("tehran", "car")
        print(f"{len(listings)} listing found")
        if listings:
            print("first listing url:", listings[0].url)
            result = await scraper.get_listing_detail(listings[0].url)
            if result.detail:
                print(result.detail)
            else:
                print(f"failed to fetch detail: {result.error}")
        else:
            print("هیچ آگهی‌ای پیدا نشد — احتمالاً سلکتور خراب یا صفحه بلاک شده")

asyncio.run(main())