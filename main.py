import os
import sys
import requests
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import tweepy
from uk_covid19 import Cov19API
from google.cloud import storage
from matplotlib.ticker import MaxNLocator

try:
    twitter_oath_key = os.environ["oath_key"]
    twitter_oath_secret = os.environ["oath_secret"]
    twitter_access_key = os.environ["access_key"]
    twitter_access_secret = os.environ["access_secret"]
    mastodon_secret = os.environ["mastodon_secret"]
    graph_file = os.environ["graph_file"]
    storage_bucket = os.environ["storage_bucket"]
    # print("Environment set up correctly")
except:
    print("Environment variables not imported")
    raise

area = ["areaType=nation", "areaName=england"]
structure = {"date": "date", "data": "newDeaths28DaysByDeathDateAgeDemographics"}


def covid19_tweet(event, context):
    if check_last_modified():
        print("Data updated")
        raw_data = get_covid_data()
        df, latest_date, cumulative_deaths = create_data(raw_data)
        create_graph(df, latest_date)
        create_tweet(cumulative_deaths, latest_date)
        create_toot(cumulative_deaths, latest_date)
    else:
        print("Data has not been updated")


def get_covid_data():
    api = Cov19API(filters=area, structure=structure)
    data = api.get_json()
    return data


def check_last_modified():
    # print("check_last_modified running")
    last_modified_from_site = get_last_modified()
    local_last_modified = get_local_last_modified()
    # print("API last modified", last_modified_from_site)
    # print("Local last modified", local_last_modified)
    if last_modified_from_site > local_last_modified:
        return True
    else:
        # write_last_modified_to_file(local_last_modified)
        return False


def get_last_modified():
    api = Cov19API(filters=area, structure=structure)
    api_timestamp = api.last_update
    # print("API timestamp", api_timestamp)     
    last_modified_datetime = datetime.strptime(
        api_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return last_modified_datetime


def get_local_last_modified():
    try:
        # print("getting local_last_modified")
        local_last_modified = download_blob(storage_bucket, "local_child_deaths_modified")
    except:
        print("Could not access", str(storage_bucket), "local_child_deaths_modified")
        local_last_modified = "1970-01-01 00:00:00"
    return datetime.strptime(local_last_modified, "%Y-%m-%d %H:%M:%S")


def write_last_modified_to_file(last_modified):
    last_modified_string = str(last_modified)
    upload_blob(storage_bucket, "local_child_deaths_modified", last_modified_string)


def check_data_is_current(data):
    latest_date_str = data["data"][0]["date"]
    latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d")
    todays_date = datetime.now()
    return latest_date.date() == todays_date.date()


def create_data(json_data):
    # format json
    new_data = []
    for d in json_data['data']:
        date = pd.to_datetime(d['date'])
        for i in d['data']:
            age = i["age"]
            deaths = i["deaths"]
            new_data.append({'date': date, age : deaths})
    # create dataframe
    df = pd.DataFrame(new_data)
    df.drop(columns=['00_59', "60+",], inplace=True)
    latest_date = df['date'].max()

    # consolidate dates
    df = df.groupby(df['date']).sum()

    # add child sum & group by month
    df['child_deaths'] = df[['00_04', '05_09', '10_14', '15_19']].sum(axis=1)
    df = df.groupby(pd.Grouper(freq="M")).sum()

    latest_date = latest_date.strftime("%d/%m/%Y")
    cumulative_deaths = int(df['child_deaths'].cumsum().max())

    return df, latest_date, cumulative_deaths
    


def create_graph(df, latest_date):
    total_deaths = int(df['child_deaths'].cumsum().max())
    fig, ax = plt.subplots()
    locator = mdates.MonthLocator(bymonthday=-1)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b-%y'))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.ylabel('deaths')
    plt.title("Covid-19 Deaths England 0-19 years\n" + str(total_deaths) + " to " + latest_date)
    ax.bar(df.index, df['child_deaths'], width=20, align='center')
    plt.tight_layout()
    fig.autofmt_xdate(rotation=90, ha='center')
    plt.savefig(graph_file)


def create_tweet(cumulative_deaths, latest_date):
    # Authenticate to Twitter
    auth = tweepy.OAuthHandler(twitter_oath_key, twitter_oath_secret)
    auth.set_access_token(twitter_access_key, twitter_access_secret)

    api = tweepy.API(auth)

    try:
        api.verify_credentials()
        # print("Authentication OK")
    except:
        print("Error during authentication")

    # send tweet
    media = api.media_upload(graph_file)
    media_id = media.media_id_string
    media_id
    tweet_text = (
        "Latest COVID-19 children (0-19 year) deaths for England - "
        + str(cumulative_deaths)
        + ".\nLast updated on " + str(latest_date) + "\n#COVID19 #python #pandas\nnewDeaths28DaysByDeathDateAgeDemographics\nhttps://coronavirus.data.gov.uk/details/developers-guide/main-api#structure-metrics"
    )
    api.update_status(tweet_text, media_ids=[media.media_id])
    print("Tweet sent")
    write_last_modified_to_file(get_last_modified())


def create_toot(cumulative_deaths, latest_date):
    auth = {'Authorization': f"Bearer {mastodon_secret}"}
    # upload media
    try:
        url = "https://mastodon.social/api/v2/media"
        media_info = ('graph.png', open(graph_file, 'rb'), 'image/png')
        media_description = (
            "Latest COVID-19 children (0-19 year) deaths for England - "
            + str(cumulative_deaths)
            + ".\nLast updated on " + str(latest_date) + "\n#COVID19 #python #pandas\nnewDeaths28DaysByDeathDateAgeDemographics\nhttps://coronavirus.data.gov.uk/details/developers-guide/main-api#structure-metrics"
        )
        r = requests.post(url, files={'file': media_info}, headers=auth, params = {'description' : media_description})
        media_id = r.json()['id']
        #print(f"Image uploaded to Mastodon - media_id = {media_id}")
    except:
        print("Error uploading media to mastodon")
    
    # send toot
    try:
        url = "https://mastodon.social/api/v1/statuses"
        toot_text = (
            "Latest COVID-19 children (0-19 year) deaths for England - "
            + str(cumulative_deaths)
            + ".\nLast updated on " + str(latest_date) + "\n#COVID19 #python #pandas\nnewDeaths28DaysByDeathDateAgeDemographics\nhttps://coronavirus.data.gov.uk/details/developers-guide/main-api#structure-metrics"
        )
        params = {'status': toot_text, 'media_ids[]': media_id}
        r = requests.post(url, data=params, headers=auth)
    except:
        print("Error sending toot to Mastodon")



def download_blob(storage_bucket, source_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(storage_bucket)
    blob = bucket.blob(source_blob_name)
    blob_string = str(blob.download_as_bytes(), 'utf-8')
    return blob_string


def upload_blob(storage_bucket, destination_blob_name, data):
    storage_client = storage.Client()
    bucket = storage_client.bucket(storage_bucket)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_string(data)
