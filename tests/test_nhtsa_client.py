"""Tests for NHTSA client — mock-based, no live network."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.services.nhtsa_client import (
    NhtsaApiError,
    NhtsaVehicle,
    _build_complaint_from_raw,
    _build_recall_from_raw,
    _safe_int,
    fetch_complaints_by_vehicle,
    fetch_recalls_by_vehicle,
)


class TestSafeInt:
    def test_valid_int(self):
        assert _safe_int(42) == 42
        assert _safe_int("42") == 42
        assert _safe_int("2024") == 2024

    def test_invalid_int(self):
        assert _safe_int(None) is None
        assert _safe_int("") is None
        assert _safe_int("abc") is None
        assert _safe_int("12.34") is None


class TestBuildComplaintFromRaw:
    def test_full_record(self):
        raw = {
            "odiNumber": "12345678",
            "make": "Ford",
            "model": "F-150",
            "modelYear": "2022",
            "component": "SERVICE BRAKES",
            "summary": "Brakes failed",
            "crash": "Y",
            "fire": "N",
            "injury": "Y",
            "death": "N",
            "dateComplaintFiled": "20230115",
            "dateIncident": "20221220",
            "ODIURL": "https://api.nhtsa.gov/complaints/complaint?odi=12345678",
        }
        record = _build_complaint_from_raw(raw)
        assert record.odi_number == "12345678"
        assert record.make == "Ford"
        assert record.model == "F-150"
        assert record.model_year == 2022
        assert record.component == "SERVICE BRAKES"
        assert record.crash == "Y"
        assert record.injury == "Y"
        assert record.death == "N"
        assert record.received_date == "20230115"
        assert record.source_url == raw["ODIURL"]

    def test_minimal_record(self):
        raw: dict[str, Any] = {}
        record = _build_complaint_from_raw(raw)
        assert record.odi_number is None
        assert record.model_year is None
        assert record.component is None
        assert record.crash == "N"


class TestBuildRecallFromRaw:
    def test_full_record(self):
        raw = {
            "NHTSACampaignNumber": "22V176000",
            "make": "Ford",
            "model": "F-150",
            "modelYear": "2022",
            "component": "SERVICE BRAKES",
            "summary": "Brake lamp issue",
            "consequence": "Risk of crash",
            "remedy": "Dealer will update software",
            "notes": "OTA update available",
            "numberVehiclesAffected": "12345",
            "reportReceivedDate": "20220315",
            "remedyUrl": "https://www.nhtsa.gov/recalls",
        }
        record = _build_recall_from_raw(raw)
        assert record.campaign_number == "22V176000"
        assert record.make == "Ford"
        assert record.model_year == 2022
        assert record.units_affected == 12345
        assert record.remedy == "Dealer will update software"

    def test_minimal_record(self):
        raw: dict[str, Any] = {}
        record = _build_recall_from_raw(raw)
        assert record.campaign_number is None
        assert record.model_year is None


class TestFetchComplaintsSuccess:
    @patch("app.services.nhtsa_client.httpx.Client")
    def test_returns_complaints(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"odiNumber": "123", "make": "Ford", "model": "F-150", "modelYear": "2022"},
                {"odiNumber": "456", "make": "Ford", "model": "F-150", "modelYear": "2022"},
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        vehicle = NhtsaVehicle(make="Ford", model="F-150", model_year=2022)
        records = fetch_complaints_by_vehicle(vehicle)

        assert len(records) == 2
        assert records[0].odi_number == "123"
        assert records[1].odi_number == "456"

    @patch("app.services.nhtsa_client.httpx.Client")
    def test_empty_results(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        vehicle = NhtsaVehicle(make="Ford", model="F-150", model_year=2022)
        records = fetch_complaints_by_vehicle(vehicle)
        assert records == []


class TestFetchComplaintsErrors:
    @patch("app.services.nhtsa_client.httpx.Client")
    def test_timeout(self, mock_client_cls):
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client_cls.return_value = mock_client

        vehicle = NhtsaVehicle(make="Ford", model="F-150", model_year=2022)
        with pytest.raises(NhtsaApiError) as exc:
            fetch_complaints_by_vehicle(vehicle)
        assert "Timeout" in str(exc.value)

    @patch("app.services.nhtsa_client.httpx.Client")
    def test_http_error(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        vehicle = NhtsaVehicle(make="Ford", model="F-150", model_year=2022)
        with pytest.raises(NhtsaApiError) as exc:
            fetch_complaints_by_vehicle(vehicle)
        assert exc.value.status_code == 500


class TestFetchRecalls:
    @patch("app.services.nhtsa_client.httpx.Client")
    def test_returns_recalls(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "NHTSACampaignNumber": "22V176",
                    "make": "Ford",
                    "model": "F-150",
                    "modelYear": "2022",
                },
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        vehicle = NhtsaVehicle(make="Ford", model="F-150", model_year=2022)
        records = fetch_recalls_by_vehicle(vehicle)

        assert len(records) == 1
        assert records[0].campaign_number == "22V176"
