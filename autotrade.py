import os
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
import json
import ta
from ta.utils import dropna
import requests


### .env 파일에 저장되어 있는 환경변수 정보를 불러옴
load_dotenv()

### 공포 탐욕 지수 API 호출
def get_fear_and_greed_index():
    url = "https://api.alternative.me/fng/"
    response = requests.get(url, verify=False)
    if response.status_code == 200:
        data = response.json()
        return data['data'][0]
    else:
        print(f"Failed to fetch Fear and Greed Index. Status code: {response.status_code}")
        return None
    
### SerpAPI를 이용하여 Bitcoin 관련 뉴스 헤드라인 조회
def get_bitcoin_news():
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": "btc",
        "api_key": serpapi_key
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raises a HTTPError if the status is 4xx, 5xx
        data = response.json()
        
        news_results = data.get("news_results", [])
        headlines = []
        for item in news_results:
            headlines.append({
                "title": item.get("title", ""),
                "date": item.get("date", "")
            })
        
        return headlines[:5]  # 최신 5개의 뉴스 헤드라인만 반환
    except requests.RequestException as e:
        print(f"Error fetching news: {e}")
        return []

### TA 라이브러리를 이용하여 df 데이터에 보조지표 추가
### 추가한 보조 지표 : 볼린저 밴드, RSI, MACD, 이동평균선 
def add_indicators(df):
    # 볼린저 밴드
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_bbm'] = indicator_bb.bollinger_mavg()
    df['bb_bbh'] = indicator_bb.bollinger_hband()
    df['bb_bbl'] = indicator_bb.bollinger_lband()

    # RSI
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()

    # MACD
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()
    
    # 이동평균선
    df['sma_20'] = ta.trend.SMAIndicator(close=df['close'], window=20).sma_indicator()
    df['ema_12'] = ta.trend.EMAIndicator(close=df['close'], window=12).ema_indicator()
    
    return df

### 자동 트레이드 메서드
def ai_trading():
    # Upbit 객체 생성
    accessKey = os.getenv("UPBIT_ACCESS_KEY")
    secretKey = os.getenv("UPBIT_SECRET_KEY") 
    upbit = pyupbit.Upbit(accessKey, secretKey)

    # 1. 현재 투자 상태 조회 (KRW, BTC 만 조회)
    all_balances = upbit.get_balances()
    filtered_balances = [balance for balance in all_balances if balance['currency'] in ['BTC','KRW']]
    # print(json.dumps(filtered_balances))

    # 2. KRW-BTC 오더북 (호가 데이터) 조회
    orderbook = pyupbit.get_orderbook("KRW-BTC")
    # print(json.dumps(orderbook))

    # 3. 차트 데이터 조회 및 보조지표 추가
    # 30일 일봉 데이터
    df_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=30)
    # NaN 값 제거
    df_daily = dropna(df_daily)     
    df_daily = add_indicators(df_daily)
    
    # 24시간 시간봉 데이터
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)
    # NaN 값 제거
    df_hourly = dropna(df_hourly)
    df_hourly = add_indicators(df_hourly)

    # print(df_daily)
    # print(df_hourly)

    # 4. 공포 탐욕 지수 가져오기
    fear_greed_index = get_fear_and_greed_index()

    # 5. 뉴스 헤드라인 가져오기
    news_headlines = get_bitcoin_news()

    # AI에게 데이터 제공하고 판단 받기
    client = OpenAI()

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
        {
        "role": "system",
        "content": """You are an expert in Bitcoin investing. Analyze the provided data including technical indicators, market data, recent news headlines, and the Fear and Greed Index. Tell me whether to buy, sell, or hold at the moment. Consider the following in your analysis:
        - Technical indicators and market data
        - Recent news headlines and their potential impact on Bitcoin price
        - The Fear and Greed Index and its implications
        - Overall market sentiment
        
        Response in json format.

        Response Example:
        {"decision": "buy", "reason": "some technical, fundamental, and sentiment-based reason"}
        {"decision": "sell", "reason": "some technical, fundamental, and sentiment-based reason"}
        {"decision": "hold", "reason": "some technical, fundamental, and sentiment-based reason"}"""
        },
        {
        "role": "user",
        "content": f"""Current investment status: {json.dumps(filtered_balances)}
            Orderbook: {json.dumps(orderbook)}
            Daily OHLCV with indicators (30 days): {df_daily.to_json()}
            Hourly OHLCV with indicators (24 hours): {df_hourly.to_json()}
            Recent news headlines: {json.dumps(news_headlines)}
            Fear and Greed Index: {json.dumps(fear_greed_index)}"""
        }
        ],
        response_format={
        "type": "json_object"
        }
    )
    result = response.choices[0].message.content

    # AI의 판단에 따라 실제로 자동매매 진행하기
    result = json.loads(result)

    print("### AI Decision: ", result["decision"].upper(), "###")
    print(f"### Reason: {result['reason']} ###")

    if result["decision"] == "buy":
        my_krw = upbit.get_balance("KRW")
        if my_krw*0.9995 > 5000:
            print("### Buy Order Executed ###")
            print(upbit.buy_market_order("KRW-BTC", my_krw * 0.9995))
        else:
            print("### Buy Order Failed: Insufficient KRW (less than 5000 KRW) ###")
    elif result["decision"] == "sell":
        my_btc = upbit.get_balance("KRW-BTC")
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        if my_btc*current_price > 5000:
            print("### Sell Order Executed ###")
            print(upbit.sell_market_order("KRW-BTC", my_btc))
        else:
            print("### Sell Order Failed: Insufficient BTC (less than 5000 KRW worth) ###")
    elif result["decision"] == "hold":
        print("### Hold Position ###")


ai_trading()