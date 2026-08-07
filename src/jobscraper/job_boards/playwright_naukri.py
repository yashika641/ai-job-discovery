from playwright.sync_api import sync_playwright
import json

SEARCH_URL = "https://www.naukri.com/ai-jobs?k=ai&nignbevent_src=jobsearchDeskGNB&experience=2&wfhType=2&wfhType=3&jobAge=1&ctcFilter=6to10"


def capture_session():
    captured = {
        "url": None,
        "headers": None,
        "cookies": None,
    }

    with sync_playwright() as p:

        print("Launching Chrome...")

        browser = p.chromium.launch(
            headless=False,      # Change to True after testing
            channel="chrome",
            slow_mo=200
        )

        context = browser.new_context()

        page = context.new_page()

        def intercept(request):
            if "/jobapi/v3/search" in request.url:
                print("\n==============================")
                print("FOUND NAUKRI API")
                print("==============================")
                print(request.url)

                captured["url"] = request.url
                captured["headers"] = dict(request.headers)

        # Register BEFORE navigation
        page.on("request", intercept)

        print("Opening Naukri...")

        page.goto(
            SEARCH_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        print("Waiting for API request...")
        page.wait_for_timeout(10000)
        # Wait until at least one job card is visible
        page.wait_for_selector(".srp-jobtuple-wrapper", timeout=30000)

        cards = page.locator(".srp-jobtuple-wrapper")

        count = cards.count()

        print(f"\nFound {count} jobs on current page\n")

        jobs = []

        for i in range(count):

            card = cards.nth(i)

            def safe(locator):
                try:
                    return locator.inner_text().strip()
                except:
                    return ""

            def safe_attr(locator, attr):
                try:
                    return locator.get_attribute(attr)
                except:
                    return ""

            title = safe(card.locator("a.title"))

            company = safe(card.locator(".comp-name"))

            experience = safe(card.locator(".expwdth"))

            location = safe(card.locator(".locWdth"))

            salary = safe(card.locator(".sal-wrap"))

            skills = []

            try:
                skill_locator = card.locator(".tags-gt li")
                for j in range(skill_locator.count()):
                    skills.append(skill_locator.nth(j).inner_text().strip())
            except:
                pass

            url = safe_attr(card.locator("a.title"), "href")

            jobs.append(
                {
                    "title": title,
                    "company": company,
                    "experience": experience,
                    "location": location,
                    "salary": salary,
                    "skills": ", ".join(skills),
                    "url": url,
                }
            )

            print("=" * 80)
            print(title)
            print(company)
            print(experience)
            print(location)
            print(salary)
            print(skills)
            print(url)
        cookies = context.cookies()

        captured["cookies"] = {
            c["name"]: c["value"]
            for c in cookies
        }

        browser.close()

    with open("session.json", "w") as f:
        json.dump(captured, f, indent=4)

    print("\nSession saved.")

    if captured["url"]:
        print("✓ API captured successfully")
    else:
        print("✗ API NOT captured")


if __name__ == "__main__":
    capture_session()