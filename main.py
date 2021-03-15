import requests
import collections
import string
import random
import os
import alpaca_trade_api as tradeapi
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timezone
from urllib.request import urlopen

env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

def request_response(request):
    """Responds to any HTTP request.
    Args:
        request (flask.Request): HTTP request object.
    Returns:
        The response text or any set of values that can be turned into a
        Response object using
        `make_response <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>`.
    """
    # Set CORS headers for the preflight request
    if request.method == 'OPTIONS':
        # Allows GET requests from any origin with the Content-Type
        # header and caches preflight response for an 3600s
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)
    # Set CORS headers for the main request
    headers = {
        'Access-Control-Allow-Origin': '*'
    }

    # Default responses
    responseFail = {
        "success": "false" 
    }
    response = {
        "success": "true" 
    }
    # Get the tweet from the post request
    request_json = request.get_json()
    tweet = request_json['tweet'].lower()
    print(tweet) # for logging
    
    success = parse_tweet(tweet)

    if success:
        return (json.dumps(response, default=str), 200, headers)
    return (json.dumps(responseFail, default=str), 200, headers)

def parse_tweet(tweet):
    
    alpaca = tradeapi.REST(
        os.getenv("ACCESS_KEY_ID"),
        os.getenv("SECRET_ACCESS_KEY"),
        base_url="https://api.alpaca.markets"
    )

    # Get the tweet
    # Get the stock tickers from the tweet
    tickers = getStockTicker(tweet)
    print(tickers)

    # Go through each ticker and position size them
    # Position size based on:
    # account risk = 1 %
    # trade risk = 15%
    # position size = account risk / (price * traderisk)
    purchases = collections.defaultdict(int)
    for ticker in tickers:
        qty, price = getPositionSize(ticker, alpaca)
        if (qty > 0):
            purchases[ticker] = (qty, price)

    if len(purchases) <= 0:
        print("No purchases to be made")
        return False

    # Create a unique ID for this order
    trimmed_tweet = tweet.lower()[0:min(len(tweet), 20)]
    unique_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

    # Buy shares to cover our position sizes
    for ticker in purchases:
        quantity, price = purchases[ticker]
        if not purchaseTicker(alpaca, ticker, quantity, price, unique_id):
            print(f"Error purchasing {ticker} with potential ID: {ticker}+{unique_id}")
        else:
            print(f"Purchased {quantity} of {ticker} for {price}")

    account = alpaca.get_account()
    # Add trailing stop orders if we can
    while len(purchases) > 0 and account.daytrade_count < 3 and attempts > 0:
        ticker = list(purchases.keys())[0]
        quantity, price = purchases[ticker]

        if trailingStopTicker(alpaca, ticker, quantity, price, unique_id):
            purchases.pop(ticker)
            continue

        time.sleep(1)

    if len(purchases) > 0:
        return False

    return True

def trailingStopTicker(alpaca, ticker, quantity, price, unique_id):
    order = alpaca.get_order_by_client_order_id(
        f'{ticker}+{unique_id}'
    )

    if order.status == "filled":
        # Set a trailing stop loss 
        alpaca.submit_order(
            symbol=ticker,
            qty=quantity,
            side='sell',
            type='trailing_stop',
            trail_percent=10,  # stop price will be hwm*0.90
            time_in_force='gtc',
        )

        return True
    
    return False

def purchaseTicker(alpaca, ticker, quantity, price, unique_id):
    if quantity <= 0:
        return False
    account = alpaca.get_account()

    try:
        # If we can't day trade, we'll do a bracket order
        if account.daytrade_count >= 3:
            alpaca.submit_order(
                symbol=ticker,
                qty=quantity,
                side='buy',
                type='market',
                time_in_force='gtc',
                order_class='bracket',
                take_profit={'limit_price': price * 1.05},
                stop_loss={
                    'stop_price': price * 0.90,
                    'limit_price': price * 0.85
                }
            )
        # If we can daytrade, we'll do a basic buy, and then add a trailing stop later
        else:
            alpaca.submit_order(
                symbol=ticker,
                qty=quantity,
                side='buy',
                type='market',
                time_in_force='gtc',
                client_order_id = f'{ticker}+{unique_id}'
            )
    except Exception as e:
        print(e)
        return False
    
    return True

def getPositionSize(ticker, alpaca):
    account = alpaca.get_account()

    # Get account equity
    equity = float(account.equity)
    cash = float(account.cash)

    # Get price of the ticker
    try:
        bars = alpaca.get_barset(ticker, "minute", 1)
        price = float(bars[ticker][0].c)
    except:
        print("couldn't get info for ticker " + ticker)
        return 0, 0

    accountRisk = equity * 0.05 # the max we're willing to lose overall
    tradeRisk = 0.1 # How much we're willing to lose on one trade
    positionSize = accountRisk / (tradeRisk * price) # number of shares we can buy
    
    try:
        currentPosition = alpaca.get_position(ticker).qty
    except:
        currentPosition = 0

    # Check if we can even buy this many 
    if (positionSize - currentPosition) * price > cash:
        print(f"Can't afford to buy {(positionSize - currentPosition)} shares. Only have {cash} in cash.")
        return 0,0

    # Unfortunately, Alpaca doesn't support fractional shares yet (but should soon)
    if (int(positionSize) - int(currentPosition) > 0):
        print(f"Planning to to buy {int(positionSize - currentPosition)} shares of {ticker}")
        return int(positionSize) - int(currentPosition), price

    return 0,0

def getAllTickers():

    r = urlopen("https://www.sec.gov/include/ticker.txt")

    tickers = {line.decode('UTF-8').split("\t")[0].upper() for line in r}
    return tickers

def getStockTicker(tweet):
    allTickers = getAllTickers()
    tickers = set()
    for word in tweet.split(" "):
        if '$' not in word:
            continue
        word = word.replace("$", "")
        word = word.translate(str.maketrans('', '', string.punctuation))
        if word.upper() not in allTickers:
            continue
        tickers.add(word.upper())
    return tickers

def submitOrder(alpaca, qty, stock, side, resp):
    if(qty > 0):
        try:
            alpaca.submit_order(stock, qty, "side", "market", "day")
            print("Market order of | " + str(qty) + " " + stock + " " + side + " | completed.")
        except:
            print("Market Order of | " + str(qty) + " " + stock + " " + side + " | did not go through.")

if __name__ == "__main__":
    parse_tweet("$GHSI to the moon")