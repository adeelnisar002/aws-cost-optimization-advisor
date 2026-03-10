from models.schemas import RemediationInputItem
from utils.opportunity_detector import detect_opportunities


def test_detects_high_ec2_cost_opportunity() -> None:
    items = [
        RemediationInputItem(
            service_name="AmazonEC2",
            monthly_cost=1000.0,
            usage_type="BoxUsage:m5.large",
            region="eu-central-1",
            anomaly_flag=False,
        ),
        RemediationInputItem(
            service_name="AmazonS3",
            monthly_cost=100.0,
            usage_type="TimedStorage-ByteHrs",
            region="eu-central-1",
            anomaly_flag=False,
        ),
    ]

    opps = detect_opportunities(items)
    categories = {o.category for o in opps}

    assert "ec2_high_cost" in categories
    assert any(o.service == "AmazonEC2" for o in opps)



