from flask import Flask, jsonify, request
from fractions import Fraction
import json
from pint import UnitRegistry
from unicodedata import numeric
from subprocess import Popen, PIPE

from ingreedypy import Ingreedy


app = Flask(__name__)
unit_registry = UnitRegistry()


def parse_descriptions_nyt(descriptions):
    env = {'PATH': '/usr/bin:/usr/local/bin', 'PYTHONPATH': '..'}
    command = ['bin/parse-ingredients.py', '--model-file', 'model/latest']
    parser = Popen(command, env=env, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    out, err = parser.communicate('\n'.join(descriptions))
    return json.loads(out)


def parse_description_ingreedypy(description):
    try:
        ingredient = Ingreedy().parse(description)
    except Exception as e:
        try:
            ingredient = Ingreedy().parse(description[e.column():])
        except Exception:
            return

    return {
        'parser': 'ingreedypy',
        'description': description,
        'product': ingredient.get('ingredient'),
        'quantity': ingredient.get('amount'),
        'units': ingredient.get('unit'),
    }


def parse_quantity(value):
    if value is None:
        return

    try:
        quantity = 0
        fragments = value.split()
        for fragment in fragments:
            if len(fragment) == 1:
                fragment = numeric(fragment)
            elif fragment[-1].isdigit():
                fragment = Fraction(fragment)
            else:
                fragment = Fraction(fragment[:-1]) + numeric(fragment[-1])
            quantity += float(fragment)
        return quantity
    except Exception:
        return None


def get_base_units(quantity):
    dimensionalities = {
        'length': unit_registry.Quantity(1, 'cm'),
        'volume': unit_registry.Quantity(1, 'ml'),
        'weight': unit_registry.Quantity(1, 'g'),
    }
    dimensionalities = {
        v.dimensionality: unit_registry.get_symbol(str(v.units))
        for k, v in dimensionalities.items()
    }
    return dimensionalities.get(quantity.dimensionality)


def parse_units(ingredient):
    quantity = unit_registry.Quantity(
        ingredient['quantity'],
        ingredient['units']
    )

    base_units = get_base_units(quantity)
    if not base_units:
        message = 'Could not find base units for quantity {}'.format(quantity)
        raise TypeError(message)

    return {
        'quantity': quantity.to(base_units).magnitude,
        'quantity_parser': ingredient['parser'] + '+pint',
        'units': unit_registry.get_symbol(base_units),
        'units_parser': ingredient['parser'] + '+pint'
    }


def merge_ingredient_field(winner, field):
    if winner.get(field) is None:
        return {}

    nested_fields = {'product'}
    parser = '{}_parser'.format(field)
    ingredient = {
        field: winner[field],
        parser: winner['parser'] if winner[field] else None,
    }
    return {field: ingredient} if field in nested_fields else ingredient


def merge_ingredients(a, b):
    a_product = not b or a and a.get('product') \
        and len(a['product']) <= len(b['product'])
    a_quantity = not b or a and a.get('quantity')
    a_units = not b or a and a.get('units')

    winners = {
        'product': a if a_product else b,
        'quantity': a if a_quantity else b,
        'units': a if a_units else b,
    }

    ingredient = {'description': winners.values()[0]['description']}
    for field in ['product', 'quantity', 'units']:
        winner = winners[field]
        merge_field = merge_ingredient_field(winner, field)
        ingredient.update(merge_field)

    try:
        units_field = parse_units(a if a_units else b)
        ingredient.update(units_field)
    except TypeError:
        raise
    except Exception:
        if b and b.get('units') and b.get('quantity'):
            try:
                units_field = parse_units(b if a_units else a)
                ingredient.update(units_field)
            except Exception:
                pass

    return ingredient


@app.route('/', methods=['POST'])
def root():
    descriptions = request.form.getlist('descriptions[]')
    descriptions = [d.encode('utf-8') for d in descriptions]
    descriptions = [d.strip().lower() for d in descriptions]

    nyt_ingredients = parse_descriptions_nyt(descriptions)
    nyt_ingredients = [{
        'parser': 'nyt',
        'description': nyt_ingredient['input'],
        'product': nyt_ingredient.get('name'),
        'quantity': parse_quantity(nyt_ingredient.get('qty')),
        'units': nyt_ingredient.get('unit'),
    } for nyt_ingredient in nyt_ingredients]

    ingredients = []
    for nyt_ingredient in nyt_ingredients:
        description = nyt_ingredient['description']
        igy_ingredient = parse_description_ingreedypy(description)
        ingredient = merge_ingredients(nyt_ingredient, igy_ingredient)
        ingredients.append(ingredient)
    return jsonify(ingredients)
