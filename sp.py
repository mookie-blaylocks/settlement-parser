import datetime
import os
import urllib.request
import numpy
from scipy import optimize, stats
import sys
import json
import math


PRODUCT_SYMBOLS = json.loads(open("commodities.json", "r").read())
INTEREST_RATE = 0.01


def get_settlements(symbol):
    """
    Retrieve 6:00PM settlements from the CME website.
    Saves to a file named file_name.
    """

    i = datetime.datetime.now()

    if not (i.hour > 14 or (i.hour > 13 and i. minute > 29)):
        while True:
            i -= datetime.timedelta(days=1)
            if i.weekday() < 5:
                break

    exchange = PRODUCT_SYMBOLS[symbol]["exchange"]

    file_name = "{0}_{1}_{2}_{3}_settlements.txt".format(i.day, i.month, i.year, exchange)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    dest_dir = os.path.join(script_dir, "Settlements")
    if not os.path.exists(dest_dir):
        os.mkdir(dest_dir)

    file_path = os.path.join(dest_dir, file_name)

    if not os.path.isfile(file_path):
        url = "ftp://ftp.cmegroup.com/pub/settle/stl{0}".format(exchange)
        urllib.request.urlretrieve(url, file_path)
    return {"file_name": file_name, "directory": dest_dir}


def get_settlement_date(settlements):

    all_settles = os.path.join(settlements["directory"], settlements["file_name"])

    with open(all_settles, "r") as settles:

        theline = settles.readline().split('/')
        month = theline[0][-2:]
        month = int(month.strip())
        day = int(theline[1])
        year = theline[2][:2]
        year = int(year.strip()) + 2000

        settlement_date = datetime.date(day=day, month=month, year=year)

    return settlement_date


def make_expiration_dict(symbol):

    script_dir = os.path.dirname(os.path.abspath(__file__))
    exchange = PRODUCT_SYMBOLS[symbol]["exchange"]
    file_name = os.path.join(script_dir, "expiration_dates_{0}.csv".format(exchange))

    expiration_dict = {}

    with open(file_name, "r") as dates:
        while True:
            theline= dates.readline().split(",")
            try:
                contract = theline[0]
                month = int(theline[1])
                day = int(theline[2])
                year = int(theline[3])
                expiration_dict[contract] = datetime.date(year=year, month=month, day=day)
            except IndexError:
                break

    return expiration_dict


def isolate_commodity(settles, symbol):
    """
    Retrieves only the settlements of the given commodity from the settle file
    Saves these settlements in a new file.
    """

    file_name = "{0}_{1}".format(PRODUCT_SYMBOLS[symbol]["name"], settles["file_name"])
    all_settles = os.path.join(settles["directory"], settles["file_name"])
    comm_file = os.path.join(settles["directory"], file_name)

    if not os.path.isfile(comm_file):
        on = False
        output = ""

        with open(all_settles, "r") as settlements:
            while True:
                theline = settlements.readline()

                if len(theline) == 0:
                    break
                if PRODUCT_SYMBOLS[symbol]["futures"] in theline:
                    on = True
                if PRODUCT_SYMBOLS[symbol]["options"] in theline:
                    on = True
                if PRODUCT_SYMBOLS[symbol]["has short-dated"]:
                    if PRODUCT_SYMBOLS[symbol]["short-dated"] in theline:
                        on = True

                if on and "EST.VOL" in theline:
                    output += "\n"
                    on = False
                if on and ("Minneapolis" in theline or "Kansas City" in theline or "Mini-Sized" in theline):
                    if not symbol == "KC":
                        on = False

                if on:
                    output += theline

        with open(comm_file, "w") as symbol_settles:
            symbol_settles.write(output)

    return {"file_name": comm_file, "directory": settles["directory"]}


def ticks_to_decimal(ticks, symbol):

    if "'" in ticks:
        try:
            cents = float(ticks.split("'")[0])
        except ValueError:
            cents = 0
        fraction = float(ticks.split("'")[1])
        fraction = fraction * PRODUCT_SYMBOLS[symbol]["tick_size"]
        if fraction > 1:
            fraction = fraction / 10 # a hack fix for commodities like T-Bonds where futures settle in thousands and options in hundredths
        return cents + fraction
    else:
        return float(ticks)


def decimal_to_ticks(decimal, symbol):

    fraction = decimal % 1
    whole = math.floor(decimal)
    return whole + ((fraction / PRODUCT_SYMBOLS[symbol]["tick_size"]) / 10)


def make_futures_dict(settles, symbol, expiration_dict):

    file_name = os.path.join(settles["directory"], settles["file_name"])
    futures_dict = {}

    with open(file_name, "r") as settlements:
        on = False

        while True:
            theline = settlements.readline()

            if on:
                l = theline.split()
                try:
                    contract = l[0]
                except IndexError:
                    on = False
                    break

                ticks = l[5]
                try:
                    open_interest = int(l[-1])
                except ValueError:
                    open_interest = 0
                try:
                    settlement = ticks_to_decimal(ticks, symbol)
                except ValueError:
                    if l[0] == "TOTAL":
                        on = False
                        break
                    print(theline)
                    print("Settlement parsing error: settlements blank")
                    sys.exit(0)
                try:
                    expiration = expiration_dict[contract]
                    futures_dict[contract] = {"price": settlement,
                                              "expiration": expiration,
                                              "open_interest": open_interest,
                                              "name": contract}
                except KeyError:
                    pass

            elif PRODUCT_SYMBOLS[symbol]["futures"] in theline:
                on = True

            if "TREASURY BOND" in theline:
                print(theline)

    return futures_dict


def calc_call_delta(S, K, T, D1):
    delta = 0
    if T <= 0:
        if K < S:
            return 1
        else:
            return 0
    greeks = {}
    delta = stats.norm.cdf(D1)
    return delta


def calc_put_delta(S, K, T, D1):
    delta = 0
    greeks = {}

    if T <= 0:
        if K > S:
            return 1
        else:
            return 0

    delta = -1 * stats.norm.cdf(-D1)
    return delta


def calc_gamma(v, S, T, D1):
    gamma = stats.norm.pdf(D1) / (S * v * numpy.sqrt(T))
    return gamma


def calc_vanna(v, D1, D2):
    vanna = (stats.norm.pdf(D1) * D2) / v
    return vanna


def calc_vega(S, D1, T):
    vega = S * stats.norm.pdf(D1) * numpy.sqrt(T)
    return vega


def make_strike_dict(S, K, T, sett, call_or_put, r=INTEREST_RATE,):
    try:
        vol = optimize.brentq(theo_BS_diff, 0.0, 2.0, args=(sett, S, K, T, r, call_or_put))
    except:
        vol = 0.1
    D1 = d1(vol, S, K, T)
    D2 = d2(vol, S, K, T)
    greeks = {}
    if call_or_put == "CALL":
        greeks["delta"] = calc_call_delta(S, K, T, D1)
    if call_or_put == "PUT":
        greeks["delta"] = calc_put_delta(S, K, T, D1)
    greeks["gamma"] = calc_gamma(vol, S, T, D1)
    greeks["vega"] = calc_vega(S, D1, T)
    greeks["vanna"] = calc_vanna(vol, D1, D2)
    greeks["volatility"] = vol

    return greeks


def black_scholes(v, S, K, T, r=INTEREST_RATE):
    """
        Returns the value of a call using the Black-Scholes model.
        S = Price of underlying
        t = years until expiration
        K = Strike price
        V = annual volatility
        r = interest rate
    """
    D1 = d1(v, S, K, T, r)
    D2 = d2(v, S, K, T, r)
    val = S * stats.norm.cdf(D1) - K * numpy.exp(-r*T) * stats.norm.cdf(D2)
    return val


def put_call_parity(v, S, K, T, r=INTEREST_RATE):
    """
        Uses put-call parity to calculate the value of a put option.
    """
    C = black_scholes(v, S, K, T, r)
    put = numpy.exp(-r*T) * K + C - S
    return put


def theo_BS_diff(x, sett, S, K, T, r=INTEREST_RATE, cac="CALL"):
    if cac == "CALL":
        diff = sett - black_scholes(x, S, K, T, r)
    else:
        diff = sett - put_call_parity(x, S, K, T, r)
    return diff


def d1(v, S, K, T, r=INTEREST_RATE):
    D1 = (numpy.log(S/K) + (r + v*v/2)*T) / (v * numpy.sqrt(T))
    return D1


def d2(v, S, K, T, r=INTEREST_RATE):
    D2 = (numpy.log(S/K) + (r - v*v/2)*T) / (v * numpy.sqrt(T))
    return D2


def make_options_dict(settles, symbol, underlying, month, expiration_date, settlement_date, match="options"):

    file_name = os.path.join(settles["directory"], settles["file_name"])
    options_dict = {"expiration_date": expiration_date, 
                    "underlying": underlying,
                    "CALL": {}, 
                    "PUT": {}}

    with open(file_name, "r") as settlements:
        on = False
        call_or_put = ""    # will be "CALL" or "PUT"
        S = underlying["price"]
        T = float((expiration_date - settlement_date).days) / 365
        prev_line = None

        while True:
            theline = settlements.readline()
            if prev_line == theline:
                break
            prev_line = theline
            if on:
                try:
                    strike = int(theline.split()[0]) / PRODUCT_SYMBOLS[symbol]["strike_divisor"]
                except(ValueError, IndexError):
                    on = False
                    continue
                else:
                    settle = theline.split()[5]

                    if settle == "CAB":
                        continue

                    settle = ticks_to_decimal(settle, symbol)

                    greeks = make_strike_dict(S=S,
                                              K=strike,
                                              T=T,
                                              sett=settle,
                                              call_or_put=call_or_put
                                              )

                    if "'" in theline.split()[-1]:
                        open_interest = 0
                    else:
                        try:
                            open_interest = int(theline.split()[-1])
                        except ValueError:
                            open_interest = 0

                    options_dict[call_or_put][strike] = greeks
                    options_dict[call_or_put][strike]["open_interest"] = open_interest
                    options_dict[call_or_put][strike]["price"] = settle


            if PRODUCT_SYMBOLS[symbol][match] in theline:
                try:
                    if month == theline.split()[1]:
                        call_or_put = theline.split()[-1]
                        on = True
                except KeyError:
                    pass

    return options_dict


def get_options_months(settles, symbol, sd=False):
    all_settles = os.path.join(settles["directory"], settles["file_name"])

    options_months = []
    with open(all_settles, "r") as settlements:
        while True:
            theline = settlements.readline()

            if len(theline) == 0:
                break

            month = None
            if sd:
                if PRODUCT_SYMBOLS[symbol]["short-dated"] in theline:
                    month = theline.split()[1]
            else:
                if PRODUCT_SYMBOLS[symbol]["options"] in theline:
                    if not PRODUCT_SYMBOLS[symbol]["has short-dated"]:
                        month = theline.split()[1]
                    elif not PRODUCT_SYMBOLS[symbol]["short-dated"] in theline:
                        month = theline.split()[1]

            if month and not month in options_months:
                options_months.append(month)

    return options_months


def match_underlying(option, futures):
    if option in futures.keys():
        return futures[option]

    months = ["JAN","FEB","MAR","APR","MAY","JUN","JLY","AUG","SEP","OCT","NOV","DEC"]
    option_month = option[:3]
    option_year = int(option[-2:])

    option_month_index = months.index(option_month)
    underlying_year = option_year
    underlying = None

    while not underlying:
        try:
            underlying_month = months[option_month_index]
        except IndexError:
            option_month_index = 0
            underlying_month = months[option_month_index]
            underlying_year += 1
        try:
            underlying = futures["{0}{1}".format(underlying_month, underlying_year)]
            break
        except KeyError:
            option_month_index += 1
            
    return underlying


def get_all_settlements(symbol):
    
    settlements = get_settlements(symbol)
    #print("Settlements retrieved")
    settlement_date = get_settlement_date(settlements)
    #print("settlement date parsed")
    expiration_dict = make_expiration_dict(symbol)
    #print("expiration dict made")
    settlements = isolate_commodity(settlements, symbol)
    #print("{0} isolated".format(symbol))
    futures = make_futures_dict(settlements, symbol, expiration_dict)
    #print("Futures parsed")
    options_months = get_options_months(settlements, symbol)
    #print("options months enumerated")
    options = {}
    for month in options_months:
        #print("parsing {0} options".format(month))
        underlying = match_underlying(month, futures)
        try:
            expiration_date = expiration_dict[month]
        except KeyError:
            print("\n\n{0} expiration date is missing from expiration_dates_{1}.csv".format(
                month, PRODUCT_SYMBOLS[symbol]['exchange']))
            print("Please add this expiration date and try again.")
            subprocess.run("echo \"Expiration date for {0} is missing from expiration dates. Please update with the dates at https://www.cmegroup.com/trading/agricultural/grain-and-oilseed/soybean_product_calendar_options.html#optionProductId=321\" | mail -s \"Options Open Interest Update Needed\" chathrel@indiana.edu", shell=True)
            sys.exit(1)
        options[month] = make_options_dict(settlements,
                                         symbol,
                                         underlying,
                                         month,
                                         expiration_date,
                                         settlement_date)

    empty_keys = []
    for key in options.keys():
        if options[key] == {"PUT": {}, "CALL": {}}:
            empty_keys.append(key)        

    for key in empty_keys:
        options.pop(key, None)
        
    short_dated = {}
    if PRODUCT_SYMBOLS[symbol]['has short-dated']:
        sd_months = get_options_months(settlements, symbol, True)
        for month in sd_months:
            option_year = int(month[-2:])
            underlying = futures["{0}{1}".format(PRODUCT_SYMBOLS[symbol]['short-dated month'], option_year)]
            try:
                expiration_date = expiration_dict[month]
            except KeyError:
                print("\n\n{0} expiration date is missing from expiration_dates_{1}.csv".format(
                    month, PRODUCT_SYMBOLS[symbol]['exchange']))
                print("Please add this expiration date and try again.")
                subprocess.run("echo \"Expiration date for {0} is missing from expiration dates. Please update with the dates at https://www.cmegroup.com/trading/agricultural/grain-and-oilseed/soybean_product_calendar_options.html#optionProductId=321\" | mail -s \"Options Open Interest Update Needed\" chathrel@indiana.edu", shell=True)
                sys.exit(1)

            short_dated[month] = make_options_dict(settlements,
                                                   symbol,
                                                   underlying,
                                                   month,
                                                   expiration_date,
                                                   settlement_date,
                                                   "short-dated")
    empty_keys = []
    for key in short_dated.keys():
        if short_dated[key] == {"PUT": {}, "CALL": {}}:
            empty_keys.append(key)

    for key in empty_keys:
        short_dated.pop(key, None)

    return {"futures": futures, "options": options, "short-dated": short_dated, "settlement_date": settlement_date}


def main(symbol):
    settlements = get_all_settlements(symbol)

    futures = settlements["futures"]
    options = settlements["options"]

    print("NOV18 800  P Delta: {0}".format(settlements['options']['NOV18']['PUT'][800.0]['delta']))
    print("NOV18 1100 C Delta: {0}".format(settlements['options']['NOV18']['CALL'][1100.0]['delta']))

    #output = "strike,price,delta,vol,open interest,gamma\n"
    #for i in ["CALL", "PUT"]:
    #    output += "{0},{1}\n".format(month, i)
    #    strikes = []
    #    for key in options[symbol][month][i].keys():
    #        strikes.append(key)

    #    strikes.sort()

    #    for strike in strikes:
    #        output += "{0},{1},{2},{3},{4},{5}\n".format(strike,
    #                                               options[symbol][month][i][strike]["price"],
    #                                               options[symbol][month][i][strike]["volatility"],
    #                                               options[symbol][month][i][strike]["open_interest"],
    #                                               options[symbol][month][i][strike]["delta"],
    #                                               options[symbol][month][i][strike]["gamma"]
    #                                               )
    #settlement_date = settlements["settlement_date"]
    #with open("{0}_{1}_as_of_{2}_{3}.csv".format(symbol, month, settlement_date.month, settlement_date.day), "w") as file:
    #        file.write(output)


if __name__ == '__main__':

    symbol = sys.argv[1]
    # month = sys.argv[2]
    main(symbol)
