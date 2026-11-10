<div align="center">

# 📊 Sales Forecasting Engine

**Predictive Time-Series Analysis, Automated Ingestion & Analytics Dashboard**

[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikit-learn&logoColor=white)](https://scikit-learn.org/)
[![Pandas](https://img.shields.io/badge/Pandas-150458?style=for-the-badge&logo=pandas&logoColor=white)](https://pandas.pydata.org/)
[![n8n Automation](https://img.shields.io/badge/Automation-n8n-EA4B71?style=for-the-badge)](https://n8n.io/)

</div>

---

## 📌 Overview

This repository hosts a multi-stage **Sales Forecasting** system designed to ingest historical transaction data, detect seasonal trends, handle outlier anomalies, and project future revenue cycles. 

It combines traditional time-series decomposition with regression modeling and automated pipeline workflows for continuous evaluation.

---

## ⚡ Core Capabilities

- 📈 **Predictive Modeling:** Regression and time-series forecasting (ARIMA / Random Forest / Gradient Boosting).
- 🧹 **Feature Engineering:** Automated lag generation, rolling window averages, weekend/holiday indicators.
- 🔄 **Automation & Ingestion:** n8n workflow integration for periodic dataset syncs and trigger-based re-training.
- 📉 **Model Diagnostics:** RMSE, MAE, and MAPE calculation against holdout test partitions.

---

## 🛠️ Project Structure

```bash
├── data/                  # Sample sales records & raw inputs
├── notebooks/             # Exploratory Data Analysis (EDA) & experimentation
├── models/                # Trained model artifacts & scalers
├── src/
│   ├── preprocess.py      # Cleaning and feature pipeline
│   ├── train.py           # Model fitting and hyperparameter tuning
│   └── evaluate.py        # Error metrics and plotting
└── requirements.txt
```

---

## 🚀 Setup & Execution

```bash
# 1. Clone repository
git clone https://github.com/devanandtb1-max/sales_forcasting.git
cd sales_forcasting

# 2. Create virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install requirements
pip install -r requirements.txt

# 4. Run the training & forecast script
python src/train.py
```

