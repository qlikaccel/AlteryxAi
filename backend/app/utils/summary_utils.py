import pandas as pd
import json
from typing import Dict, List, Any, Union
import statistics
from collections import Counter

# Hugging Face imports (optional)
try:
    from transformers import pipeline, AutoTokenizer, AutoModelForCausalLM
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    print("WARNING: Hugging Face transformers not installed. Chat features disabled.")


# =========================
# 1️⃣ LOAD DATA FROM JSON
# =========================
def load_data_from_json(json_input: Union[str, dict, list]) -> pd.DataFrame:
    """
    Load data from JSON input (file path, dict, or list)
    """
    if isinstance(json_input, str):
        with open(json_input, "r") as f:
            data = json.load(f)
    else:
        data = json_input

    # Handle nested data structure
    if isinstance(data, dict) and "data" in data:
        data = data["data"]

    return pd.DataFrame(data)


# =========================
# 2️⃣ EXTRACT GENERIC METRICS
# =========================
def extract_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Extract key metrics from dataframe
    """
    metrics = {}

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = df.select_dtypes(include="object").columns.tolist()

    # Basic metrics
    metrics["Total Records"] = len(df)
    
    # Numeric metrics
    if numeric_cols:
        first_numeric = numeric_cols[0]
        metrics["Total Value"] = round(float(df[first_numeric].sum()), 2)
        metrics["Average Value"] = round(float(df[first_numeric].mean()), 2)
        metrics["Min Value"] = round(float(df[first_numeric].min()), 2)
        metrics["Max Value"] = round(float(df[first_numeric].max()), 2)

    # Top categories
    if categorical_cols and numeric_cols:
        first_cat = categorical_cols[0]
        first_num = numeric_cols[0]
        top_cats = (
            df.groupby(first_cat)[first_num]
            .sum()
            .sort_values(ascending=False)
            .head(5)
        )
        metrics["Top Categories"] = top_cats.to_dict()

    # Column summary
    metrics["Column Info"] = {
        "Numeric Columns": numeric_cols,
        "Categorical Columns": categorical_cols,
        "Total Columns": len(df.columns)
    }

    return metrics


# =========================
# 3️⃣ BUILD SUMMARY TEXT
# =========================
def build_summary_text(data: Union[List[Dict], Dict], table_name: str = "Data") -> str:
    """
    Build a professional summary text from data
    """
    try:
        df = load_data_from_json(data)
        metrics = extract_metrics(df)

        summary_lines = [
            f"📊 {table_name} Summary Report",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Total Records: {metrics.get('Total Records', 0)}",
        ]

        # Add numeric metrics
        if "Total Value" in metrics:
            summary_lines.append(f"Total Value: {metrics['Total Value']}")
            summary_lines.append(f"Average Value: {metrics['Average Value']}")
            summary_lines.append(f"Range: {metrics['Min Value']} - {metrics['Max Value']}")

        # Add top categories
        if "Top Categories" in metrics:
            summary_lines.append(f"\nTop Categories:")
            for cat, val in list(metrics['Top Categories'].items())[:3]:
                summary_lines.append(f"  • {cat}: {val}")

        return "\n".join(summary_lines)

    except Exception as e:
        return f"Error generating summary: {str(e)}"


# =========================
# 4️⃣ GENERATE STRUCTURED SUMMARY
# =========================
def generate_summary(data: Union[List[Dict], Dict], table_name: str = "Data") -> Dict[str, Any]:
    """
    Generate a complete summary from table data
    Returns structured summary data
    """
    try:
        df = load_data_from_json(data)
        metrics = extract_metrics(df)

        return {
            "success": True,
            "table_name": table_name,
            "metrics": metrics,
            "summary_text": build_summary_text(data, table_name),
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": df.columns.tolist()
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "table_name": table_name
        }


# =========================
# 5️⃣ BATCH SUMMARY FOR MULTIPLE TABLES
# =========================
def generate_batch_summary(tables_data: Dict[str, Union[List[Dict], Dict]]) -> Dict[str, Any]:
    """
    Generate summaries for multiple tables at once
    """
    results = {}
    
    for table_name, data in tables_data.items():
        results[table_name] = generate_summary(data, table_name)
    
    return {
        "success": True,
        "total_tables": len(tables_data),
        "summaries": results
    }


# =========================
# 6️⃣ UTILITY FUNCTIONS
# =========================
def get_data_quality_score(df: pd.DataFrame) -> float:
    """
    Calculate data quality score (0-100)
    Based on missing values and data completeness
    """
    total_cells = len(df) * len(df.columns)
    missing_cells = df.isna().sum().sum()
    
    quality = ((total_cells - missing_cells) / total_cells) * 100 if total_cells > 0 else 0
    return round(quality, 2)


def get_data_preview(data: Union[List[Dict], Dict], rows: int = 5) -> List[Dict]:
    """
    Get a preview of the data (first N rows)
    """
    try:
        df = load_data_from_json(data)
        return df.head(rows).to_dict('records')
    except Exception as e:
        return []


# =========================
# 7️⃣ HUGGING FACE INTEGRATION
# =========================
class HuggingFaceHelper:
    """Helper class for Hugging Face model interactions"""
    
    _summarizer = None
    _chat_model = None
    _tokenizer = None
    
    @classmethod
    def get_summarizer(cls):
        """Get or load the summarizer model"""
        if not HF_AVAILABLE:
            return None
        
        if cls._summarizer is None:
            try:
                cls._summarizer = pipeline(
                    "summarization",
                    model="facebook/bart-large-cnn"
                )
            except Exception as e:
                print(f"Error loading summarizer: {e}")
                # Fallback to simpler model
                try:
                    cls._summarizer = pipeline(
                        "text2text-generation",
                        model="google/flan-t5-small"
                    )
                except:
                    return None
        
        return cls._summarizer
    
    @classmethod
    def get_chat_model(cls):
        """Get or load the chat model"""
        if not HF_AVAILABLE:
            return None
        
        if cls._chat_model is None:
            try:
                model_name = "gpt2"  # Lightweight model
                cls._tokenizer = AutoTokenizer.from_pretrained(model_name)
                cls._chat_model = AutoModelForCausalLM.from_pretrained(model_name)
            except Exception as e:
                print(f"Error loading chat model: {e}")
                return None
        
        return cls._chat_model
    
    @classmethod
    def generate_hf_summary(cls, fact_text: str, max_length: int = 150) -> str:
        """
        Generate summary using Hugging Face model
        """
        if not HF_AVAILABLE:
            return build_summary_text([], "Data")
        
        try:
            summarizer = cls.get_summarizer()
            if summarizer is None:
                return fact_text
            
            # For long text, truncate first
            if len(fact_text) > 1024:
                fact_text = fact_text[:1024]
            
            # Use appropriate pipeline based on model type
            try:
                result = summarizer(fact_text, max_length=max_length, min_length=50, do_sample=False)
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get('summary_text', fact_text)
                elif isinstance(result, dict):
                    return result.get('generated_text', fact_text)
                else:
                    return fact_text
            except Exception as e:
                # If summarization fails, return original text
                return fact_text
        
        except Exception as e:
            print(f"Error generating summary: {e}")
            return fact_text
    
    @classmethod
    def chat_about_data(cls, question: str, metrics: Dict[str, Any]) -> str:
        """
        Chat about the table data using Hugging Face
        """
        if not HF_AVAILABLE:
            return "Hugging Face not available. Please install transformers."
        
        try:
            # Create context from metrics
            context = format_metrics_for_chat(metrics)
            
            # Combine context and question
            prompt = f"""Based on the following data metrics, answer the question.

Data Metrics:
{context}

Question: {question}

Answer:"""
            
            try:
                # Try using summarizer for Q&A style response
                summarizer = cls.get_summarizer()
                if summarizer:
                    response = summarizer(prompt, max_length=100, min_length=30, do_sample=False)
                    if isinstance(response, list) and len(response) > 0:
                        return response[0].get('summary_text', "I couldn't generate a response.")
                
                # Fallback: Generate response using pipeline
                qa_pipeline = pipeline("text-generation", model="gpt2")
                result = qa_pipeline(prompt, max_length=100, num_return_sequences=1)
                if result and len(result) > 0:
                    return result[0].get('generated_text', '').replace(prompt, '').strip()
                
            except Exception as e:
                print(f"Model generation error: {e}")
                # Fallback to rule-based response
                return generate_rule_based_response(question, metrics)
        
        except Exception as e:
            print(f"Error in chat: {e}")
            return f"I encountered an error processing your question: {str(e)}"


def format_metrics_for_chat(metrics: Dict[str, Any]) -> str:
    """Format metrics for chat context"""
    text = ""
    for key, value in metrics.items():
        if isinstance(value, dict):
            text += f"{key}:\n"
            for k, v in value.items():
                text += f"  - {k}: {v}\n"
        else:
            text += f"{key}: {value}\n"
    return text


def generate_rule_based_response(question: str, metrics: Dict[str, Any]) -> str:
    """Generate response based on rules when models fail"""
    question_lower = question.lower()
    
    # Analyze question and generate response
    if any(word in question_lower for word in ['total', 'sum', 'how many']):
        total = metrics.get('Total Records', 'unknown')
        return f"The total number of records in this dataset is {total}."
    
    elif any(word in question_lower for word in ['average', 'mean', 'avg']):
        avg = metrics.get('Average Value', 'unknown')
        return f"The average value is {avg}."
    
    elif any(word in question_lower for word in ['highest', 'maximum', 'max', 'largest']):
        max_val = metrics.get('Max Value', 'unknown')
        return f"The highest value in the dataset is {max_val}."
    
    elif any(word in question_lower for word in ['lowest', 'minimum', 'min', 'smallest']):
        min_val = metrics.get('Min Value', 'unknown')
        return f"The lowest value in the dataset is {min_val}."
    
    elif any(word in question_lower for word in ['category', 'top', 'categories']):
        top_cats = metrics.get('Top Categories', {})
        if top_cats:
            top_cat = list(top_cats.items())[0]
            return f"The top category is {top_cat[0]} with a value of {top_cat[1]}."
        return "Category information is not available."
    
    elif any(word in question_lower for word in ['quality', 'missing', 'complete']):
        return "This data quality information is not currently available. Please use /summary/quality endpoint."
    
    else:
        return f"I analyzed the data metrics. Could you be more specific? You can ask about total records, averages, maximum/minimum values, or top categories."


def create_data_chat_context(df: pd.DataFrame, metrics: Dict[str, Any]) -> str:
    """Create a comprehensive context string for chat"""
    context = "📊 Data Context:\n"
    context += f"Rows: {len(df)}\n"
    context += f"Columns: {', '.join(df.columns.tolist())}\n"
    
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if numeric_cols:
        context += f"Numeric Columns: {', '.join(numeric_cols)}\n"
    
    categorical_cols = df.select_dtypes(include="object").columns.tolist()
    if categorical_cols:
        context += f"Categorical Columns: {', '.join(categorical_cols)}\n"
    
    context += "\nMetrics:\n"
    for key, value in metrics.items():
        if not isinstance(value, dict):
            context += f"- {key}: {value}\n"
    
    return context