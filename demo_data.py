"""Made-up sample menus used when no Sodexo API key is configured.

This is NOT real GCSU data. It exists so you can see and work on the UI before
the real API is hooked up. The app shows a "Demo data" banner whenever it's used.
"""
import datetime as dt
import random

V, VG, PB, GF, M = "vegetarian", "vegan", "plant-based", "gluten-free", "mindful"

POOL = {
    "Breakfast": {
        "Grill": [("Scrambled Eggs", [V, GF], ["Egg"]), ("Turkey Sausage Links", [GF], []),
                  ("Buttermilk Pancakes", [V], ["Milk", "Egg", "Wheat"]), ("Bacon", [GF], []),
                  ("Breakfast Burrito", [], ["Egg", "Milk", "Wheat"])],
        "Home Zone": [("Biscuits & Sausage Gravy", [], ["Milk", "Wheat"]), ("Cheese Grits", [V, GF], ["Milk"]),
                      ("Hash Brown Patties", [VG, PB, GF], [])],
        "Bakery": [("Blueberry Muffin", [V], ["Egg", "Milk", "Wheat"]), ("Cinnamon Roll", [V], ["Milk", "Wheat"])],
        "Fresh Fruit": [("Seasonal Fruit Cup", [VG, PB, GF, M], []), ("Greek Yogurt Parfait", [V, M], ["Milk"])],
    },
    "Lunch": {
        "Grill": [("Classic Cheeseburger", [], ["Milk", "Wheat"]), ("Crispy Chicken Sandwich", [], ["Wheat", "Egg"]),
                  ("Black Bean Burger", [VG, PB], ["Wheat", "Soy"]), ("Seasoned Fries", [VG, PB, GF], [])],
        "Pizza": [("Pepperoni Pizza", [], ["Milk", "Wheat"]), ("Cheese Pizza", [V], ["Milk", "Wheat"]),
                  ("BBQ Chicken Pizza", [], ["Milk", "Wheat"])],
        "Deli": [("Turkey Club Wrap", [], ["Wheat"]), ("Chicken Caesar Wrap", [], ["Milk", "Wheat", "Fish"]),
                 ("Hummus Veggie Wrap", [VG, PB], ["Wheat", "Sesame"])],
        "Home Zone": [("Chicken Tenders", [], ["Wheat", "Egg"]), ("Mac & Cheese", [V], ["Milk", "Wheat"]),
                      ("Green Beans", [VG, PB, GF, M], []), ("Chicken Alfredo", [], ["Milk", "Wheat"])],
        "Simple Servings": [("Herb Roasted Chicken", [GF, M], []), ("Brown Rice", [VG, PB, GF, M], []),
                            ("Steamed Broccoli", [VG, PB, GF, M], [])],
    },
    "Dinner": {
        "Grill": [("Philly Cheesesteak", [], ["Milk", "Wheat"]), ("Grilled Chicken Breast", [GF, M], []),
                  ("Beyond Burger", [VG, PB], ["Wheat"])],
        "Pizza": [("Supreme Pizza", [], ["Milk", "Wheat"]), ("Buffalo Chicken Pizza", [], ["Milk", "Wheat"]),
                  ("Margherita Flatbread", [V], ["Milk", "Wheat"])],
        "Home Zone": [("Southern Fried Chicken", [], ["Wheat", "Egg"]), ("Mashed Potatoes & Gravy", [V], ["Milk", "Wheat"]),
                      ("Beef Tacos", [GF], ["Milk"]), ("Chicken Stir Fry", [], ["Soy", "Wheat"]),
                      ("Vegetable Lo Mein", [VG, PB], ["Soy", "Wheat"])],
        "Simple Servings": [("Baked Salmon", [GF, M], ["Fish"]), ("Quinoa Pilaf", [VG, PB, GF, M], []),
                            ("Roasted Zucchini", [VG, PB, GF, M], [])],
        "Dessert": [("Chocolate Chip Cookies", [V], ["Egg", "Milk", "Wheat"]), ("Banana Pudding", [V], ["Milk", "Egg", "Wheat"]),
                    ("Peach Cobbler", [V], ["Milk", "Wheat"])],
    },
    "Late Night": {
        "Grill": [("Chicken Quesadilla", [], ["Milk", "Wheat"]), ("Loaded Nachos", [V, GF], ["Milk"]),
                  ("Mozzarella Sticks", [V], ["Milk", "Wheat"])],
        "Pizza": [("Pepperoni Pizza", [], ["Milk", "Wheat"]), ("Cheese Pizza", [V], ["Milk", "Wheat"])],
    },
}


# A few items deliberately have no nutrition, like real feeds sometimes do,
# so the "Nutrition info not available" path is visible in demo mode.
NO_NUTRITION = {"Seasonal Fruit Cup", "Loaded Nachos", "Hummus Veggie Wrap", "Peach Cobbler"}
PORTIONS = ["1 EA", "4 OZ", "1 SLC", "6 OZ", "1/2 CUP", "1 CUP"]


def _demo_item(rng: random.Random, name: str, tags: list, allergens: list) -> dict:
    item = {"name": name, "description": "", "tags": tags, "allergens": allergens,
            "calories": None, "protein_g": None, "carbs_g": None, "fat_g": None, "portion": ""}
    if name in NO_NUTRITION:
        return item
    protein = rng.randint(2, 35)
    carbs = rng.randint(5, 60)
    fat = round(rng.uniform(1, 28) * 2) / 2
    item.update(calories=round(protein * 4 + carbs * 4 + fat * 9), protein_g=protein,
                carbs_g=carbs, fat_g=fat, portion=rng.choice(PORTIONS))
    return item


def demo_day(day: dt.date) -> list[dict]:
    rng = random.Random(day.toordinal())
    weekday = day.weekday()
    meal_names = ["Breakfast", "Lunch", "Dinner", "Late Night"] if weekday < 4 else \
                 ["Breakfast", "Lunch", "Dinner"] if weekday == 4 else ["Lunch", "Dinner"]
    meals = []
    for meal in meal_names:
        stations = []
        for station, dishes in POOL[meal].items():
            picks = rng.sample(dishes, k=min(len(dishes), rng.randint(2, 4)))
            stations.append({"name": station, "items": [
                _demo_item(rng, name, tags, allergens) for name, tags, allergens in picks]})
        meals.append({"name": meal, "stations": stations})
    return meals
