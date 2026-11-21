from rahbar.retrieval.report import summarize


def test_report_deterministic_and_language_sliced():
    rows = [dict(family_id='x', language=language, latency_ms=10,
                 at_10=dict(recall=1., complete_evidence=1., mrr=1., ndcg=1.))
            for language in ['ur', 'en', 'mixed']]
    report = summarize(rows)
    assert report == summarize(rows)
    assert report['slices']['overall']['families'] == 1
    assert report['slices']['overall']['questions'] == 3
    assert report['slices']['ur']['metrics_at_10']['recall']['ci95'] == [1., 1.]


def test_fallback_models_are_reported_separately():
    rows = [dict(id=str(i), family_id='f', language='en', planner_model=model,
                 latency_ms=1, at_10=dict(recall=1, complete_evidence=1, mrr=1, ndcg=1))
            for i, model in enumerate(['first', 'fallback'])]
    report = summarize(rows)
    assert len(report['conditions']) == 2
    assert all(item['report']['slices']['overall']['questions'] == 1
               for item in report['conditions'])
