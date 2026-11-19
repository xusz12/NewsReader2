from dataclasses import dataclass
from threading import RLock

import news_classification
import news_collection
import news_summaries
import periodic_reports
import topic_tracking


@dataclass
class BackgroundServices:
    classification: news_classification.Service
    collection: news_collection.Service
    reports: periodic_reports.Service
    summaries: news_summaries.Service
    topics: topic_tracking.Service
    report_lock: RLock

    def start(self, classification):
        self.classification.configure(**classification)
        self.classification.start()
        self.topics.start()
        self.reports.start()
        self.collection.start()
