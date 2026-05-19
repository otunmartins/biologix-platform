import ollama
from typing import List, Dict, Optional
import json


class OllamaClient:
    """
    A client for interacting with OLLAMA to analyze academic papers and abstracts
    using local language models.
    """
    
    def __init__(self, model_name: str = "llama3.2", host: str = "http://localhost:11434"):
        """
        Initialize the OLLAMA client.
        
        Args:
            model_name (str): Name of the OLLAMA model to use (e.g., 'llama3.1', 'mistral', 'codellama')
            host (str): OLLAMA server host URL
        """
        self.model_name = model_name
        self.host = host
        self.client = ollama.Client(host=host)
        
        # Check if model is available
        self._ensure_model_available()
    
    def _ensure_model_available(self):
        """
        Check if the specified model is available, and pull it if not.
        """
        try:
            models_response = self.client.list()
            available_models = []
            
            # Handle both possible response formats
            if 'models' in models_response:
                available_models = [model.get('name', model.get('model', '')) for model in models_response['models']]
            
            # Check if our model is available (with or without :latest suffix)
            model_available = False
            for available_model in available_models:
                if (self.model_name in available_model or 
                    available_model.startswith(self.model_name) or
                    available_model == f"{self.model_name}:latest"):
                    model_available = True
                    break
            
            if not model_available:
                print(f"Model '{self.model_name}' not found. Pulling model...")
                self.client.pull(self.model_name)
                print(f"Model '{self.model_name}' successfully pulled!")
            else:
                print(f"Model '{self.model_name}' is available.")
                
        except Exception as e:
            print(f"Error checking/pulling model: {e}")
            print("Please ensure OLLAMA is running and try again.")
    
    def analyze_abstract(self, abstract: str, analysis_type: str = "summary") -> str:
        """
        Analyze a single abstract using OLLAMA.
        
        Args:
            abstract (str): The abstract text to analyze
            analysis_type (str): Type of analysis ('summary', 'key_findings', 'methodology', 'limitations')
        
        Returns:
            str: Analysis result
        """
        prompts = {
            "summary": f"Provide a concise summary of this research abstract in 2-3 sentences:\n\n{abstract}",
            "key_findings": f"Extract and list the key findings from this research abstract:\n\n{abstract}",
            "methodology": f"Describe the methodology or approach used in this research based on the abstract:\n\n{abstract}",
            "limitations": f"Identify potential limitations or areas for future work mentioned or implied in this abstract:\n\n{abstract}",
            "research_questions": f"What research questions does this paper address? Based on this abstract:\n\n{abstract}",
            "practical_applications": f"What are the practical applications or implications of this research? Based on this abstract:\n\n{abstract}"
        }
        
        prompt = prompts.get(analysis_type, prompts["summary"])
        
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            return f"Error analyzing abstract: {e}"
    
    def ask_question(self, question: str) -> Dict[str, str]:
        """
        Ask a general question to the OLLAMA model.
        
        Args:
            question (str): The question to ask
        
        Returns:
            Dict[str, str]: Response dictionary with 'response' key
        """
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': question
                    }
                ]
            )
            return {"response": response['message']['content'].strip()}
        except Exception as e:
            return {"response": f"Error asking question: {e}"}
    
    def analyze_multiple_abstracts(self, papers: List[Dict], analysis_type: str = "summary") -> List[Dict]:
        """
        Analyze multiple abstracts and return results.
        
        Args:
            papers (List[Dict]): List of paper dictionaries with 'abstract' and other fields
            analysis_type (str): Type of analysis to perform
        
        Returns:
            List[Dict]: Papers with added analysis results
        """
        analyzed_papers = []
        
        for i, paper in enumerate(papers):
            print(f"Analyzing paper {i+1}/{len(papers)}: {paper.get('title', 'Unknown')[:50]}...")
            
            abstract = paper.get('abstract', '')
            if abstract:
                analysis = self.analyze_abstract(abstract, analysis_type)
                paper_with_analysis = paper.copy()
                paper_with_analysis[f'{analysis_type}_analysis'] = analysis
                analyzed_papers.append(paper_with_analysis)
            else:
                print(f"Skipping paper without abstract: {paper.get('title', 'Unknown')}")
        
        return analyzed_papers
    
    def compare_papers(self, papers: List[Dict]) -> str:
        """
        Compare multiple papers and identify common themes, differences, and trends.
        
        Args:
            papers (List[Dict]): List of paper dictionaries
        
        Returns:
            str: Comparison analysis
        """
        if len(papers) < 2:
            return "Need at least 2 papers for comparison."
        
        # Create a summary of all papers
        papers_summary = "Papers to compare:\n\n"
        for i, paper in enumerate(papers[:5], 1):  # Limit to first 5 papers to avoid token limits
            papers_summary += f"Paper {i}:\n"
            papers_summary += f"Title: {paper.get('title', 'Unknown')}\n"
            papers_summary += f"Abstract: {paper.get('abstract', 'No abstract')[:300]}...\n"
            papers_summary += f"Year: {paper.get('year', 'Unknown')}\n\n"
        
        prompt = f"""Analyze and compare these research papers. Identify:

1. Common themes and research areas
2. Different methodological approaches
3. Key trends over time
4. Gaps in the research
5. Potential future research directions

{papers_summary}

Provide a structured analysis covering these points."""

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            return f"Error comparing papers: {e}"
    
    def generate_research_questions(self, topic: str, papers: List[Dict]) -> str:
        """
        Generate research questions based on a topic and related papers.
        
        Args:
            topic (str): Research topic
            papers (List[Dict]): Related papers for context
        
        Returns:
            str: Generated research questions
        """
        # Create context from papers
        papers_context = f"Research topic: {topic}\n\nRelated research papers:\n\n"
        for i, paper in enumerate(papers[:3], 1):  # Use first 3 papers for context
            papers_context += f"Paper {i}: {paper.get('title', 'Unknown')}\n"
            if paper.get('abstract'):
                papers_context += f"Abstract: {paper['abstract'][:200]}...\n\n"
        
        prompt = f"""Based on the following research topic and related papers, generate 5-7 novel research questions that could advance the field. 

The questions should be:
- Specific and actionable
- Build upon existing research gaps
- Be methodologically feasible
- Address important problems in the field

{papers_context}

Please provide the research questions in a numbered list format."""

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            return f"Error generating research questions: {e}"
    
    def synthesize_literature_review(self, topic: str, papers: List[Dict]) -> str:
        """
        Generate a literature review synthesis based on papers.
        
        Args:
            topic (str): Research topic
            papers (List[Dict]): Papers to synthesize
        
        Returns:
            str: Literature review synthesis
        """
        # Prepare papers information
        papers_info = f"Topic: {topic}\n\nPapers to synthesize:\n\n"
        for i, paper in enumerate(papers, 1):
            papers_info += f"{i}. {paper.get('title', 'Unknown')} ({paper.get('year', 'Unknown')})\n"
            papers_info += f"   Authors: {', '.join(paper.get('authors', []))}\n"
            if paper.get('abstract'):
                papers_info += f"   Abstract: {paper['abstract'][:250]}...\n"
            papers_info += f"   Citations: {paper.get('citation_count', 0)}\n\n"
        
        prompt = f"""Write a comprehensive literature review synthesis for the topic "{topic}" based on the following papers. 

The synthesis should:
1. Provide an overview of the research area
2. Identify key themes and methodological approaches
3. Highlight major findings and contributions
4. Discuss limitations and gaps in current research
5. Suggest future research directions

Structure the review in clear sections with appropriate headings.

{papers_info}"""

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            return f"Error generating literature review: {e}" 