import requests
import time
from typing import List, Dict, Optional
import json


class SemanticScholarClient:
    """
    A client for interacting with the Semantic Scholar API to search for academic papers
    and retrieve abstracts related to specific topics.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Semantic Scholar client.
        
        Args:
            api_key (str, optional): Your Semantic Scholar API key for higher rate limits
        """
        self.base_url = "https://api.semanticscholar.org/graph/v1"
        self.headers = {}
        if api_key:
            self.headers["x-api-key"] = api_key
        
        # Rate limiting based on Semantic Scholar documentation:
        # - With API key: 1 request per second
        # - Without API key: 100 requests per 5 minutes (1 every 3-4 seconds)
        self.rate_limit_delay = 1.2 if api_key else 4.0  # Conservative 4 seconds for unauthenticated
        
    def search_papers(self, 
                     query: str, 
                     fields: List[str] = None, 
                     limit: int = 10,
                     year_range: str = None,
                     min_citation_count: int = None) -> Dict:
        """
        Search for papers using the Semantic Scholar API.
        
        Args:
            query (str): Search query (e.g., "machine learning", "diabetes treatment")
            fields (List[str]): Fields to retrieve (title, abstract, authors, year, etc.)
            limit (int): Maximum number of results to return
            year_range (str): Year range filter (e.g., "2020-2024" or "2023-")
            min_citation_count (int): Minimum number of citations
        
        Returns:
            Dict: API response containing search results
        """
        if fields is None:
            fields = ["title", "abstract", "authors", "year", "citationCount", 
                     "publicationDate", "journal", "url"]
        
        # Use the bulk search endpoint for better performance
        endpoint = f"{self.base_url}/paper/search/bulk"
        
        params = {
            "query": query,
            "fields": ",".join(fields),
            "limit": limit
        }
        
        if year_range:
            params["year"] = year_range
            
        if min_citation_count:
            params["minCitationCount"] = min_citation_count
            
        try:
            # Add timeout to prevent hanging requests
            response = requests.get(endpoint, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            # Rate limiting
            time.sleep(self.rate_limit_delay)
            
            return response.json()
            
        except requests.exceptions.Timeout:
            print(f"Request timed out after 30 seconds for query: {query[:50]}...")
            return {"data": [], "total": 0}
        except requests.exceptions.RequestException as e:
            print(f"Error searching papers: {e}")
            return {"data": [], "total": 0}
    
    def get_paper_details(self, paper_id: str, fields: List[str] = None) -> Dict:
        """
        Get detailed information about a specific paper.
        
        Args:
            paper_id (str): The Semantic Scholar paper ID
            fields (List[str]): Fields to retrieve
        
        Returns:
            Dict: Paper details
        """
        if fields is None:
            fields = ["title", "abstract", "authors", "year", "citationCount", 
                     "references", "citations", "journal"]
        
        endpoint = f"{self.base_url}/paper/{paper_id}"
        
        params = {
            "fields": ",".join(fields)
        }
        
        try:
            # Add timeout to prevent hanging requests
            response = requests.get(endpoint, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            # Rate limiting
            time.sleep(self.rate_limit_delay)
            
            return response.json()
            
        except requests.exceptions.Timeout:
            print(f"Request timed out after 30 seconds for paper ID: {paper_id}")
            return {}
        except requests.exceptions.RequestException as e:
            print(f"Error getting paper details: {e}")
            return {}
    
    def search_papers_by_topic(self, 
                              topic: str, 
                              max_results: int = 20,
                              recent_years_only: bool = False) -> List[Dict]:
        """
        Search for papers on a specific topic and return clean results.
        Uses intelligent search strategy with fallbacks for better results.
        
        Args:
            topic (str): Research topic to search for
            max_results (int): Maximum number of papers to return
            recent_years_only (bool): Whether to filter for recent papers (2020+) - now defaults to False
        
        Returns:
            List[Dict]: List of paper dictionaries with clean data
        """
        year_filter = "2020-" if recent_years_only else None
        
        # Strategy 1: Try with keywords (no exact quotes) and minimal citation filter
        print(f"   📊 Strategy 1: Searching with citation filter (min 1 citation)...")
        results = self.search_papers(
            query=topic,  # Remove quotes for more flexible matching
            limit=max_results,
            year_range=year_filter,
            min_citation_count=1  # Lower threshold for specialized topics
        )
        
        papers = self._process_search_results(results)
        print(f"   📊 Strategy 1 result: Found {len(papers)} papers")
        
        # If we got good results, return them
        if len(papers) >= max_results // 2:  # If we got at least half of what we wanted
            print(f"   ✅ Sufficient papers found, using Strategy 1 results")
            return papers[:max_results]
        
        # Strategy 2: If not enough results, try without citation filter
        if len(papers) < max_results // 2:
            print(f"   📊 Strategy 2: Expanding search (removing citation filter)...")
            results = self.search_papers(
                query=topic,
                limit=max_results * 2,  # Get more to compensate for filtering
                year_range=year_filter
                # No min_citation_count
            )
            new_papers = self._process_search_results(results)
            print(f"   📊 Strategy 2 result: Found {len(new_papers)} papers")
            papers = new_papers  # Use the new results
        
        # Strategy 3: If still not enough and using recent filter, try all years
        if len(papers) < max_results // 2 and recent_years_only:
            print(f"   📊 Strategy 3: Expanding to all years...")
            results = self.search_papers(
                query=topic,
                limit=max_results * 2
                # No year_range, no min_citation_count
            )
            new_papers = self._process_search_results(results)
            print(f"   📊 Strategy 3 result: Found {len(new_papers)} papers")
            papers = new_papers  # Use the new results
        
        final_count = len(papers[:max_results])
        print(f"   ✅ Final result: Returning {final_count} papers for query")
        return papers[:max_results]
    
    def _process_search_results(self, results: Dict) -> List[Dict]:
        """
        Process and clean search results from the API.
        
        Args:
            results (Dict): Raw results from search_papers
            
        Returns:
            List[Dict]: Cleaned list of papers
        """
        papers = []
        for paper in results.get("data", []):
            # Clean and structure the paper data with safe access
            journal_info = paper.get("journal") or {}
            journal_name = journal_info.get("name", "") if isinstance(journal_info, dict) else ""
            
            clean_paper = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "authors": [author.get("name", "") for author in paper.get("authors", [])],
                "year": paper.get("year"),
                "citation_count": paper.get("citationCount", 0),
                "journal": journal_name,
                "url": paper.get("url", ""),
                "paper_id": paper.get("paperId", "")
            }
            
            # Only include papers with abstracts
            if clean_paper["abstract"]:
                papers.append(clean_paper)
        
        return papers
    
    def get_recommendations(self, 
                          positive_paper_ids: List[str], 
                          negative_paper_ids: List[str] = None,
                          limit: int = 10) -> List[Dict]:
        """
        Get paper recommendations based on seed papers.
        
        Args:
            positive_paper_ids (List[str]): Paper IDs for positive examples
            negative_paper_ids (List[str]): Paper IDs for negative examples
            limit (int): Number of recommendations to return
        
        Returns:
            List[Dict]: Recommended papers
        """
        endpoint = "https://api.semanticscholar.org/recommendations/v1/papers"
        
        data = {
            "positivePaperIds": positive_paper_ids
        }
        
        if negative_paper_ids:
            data["negativePaperIds"] = negative_paper_ids
        
        params = {
            "fields": "title,abstract,authors,citationCount,year",
            "limit": limit
        }
        
        try:
            response = requests.post(endpoint, json=data, params=params, headers=self.headers)
            response.raise_for_status()
            
            # Rate limiting
            time.sleep(self.rate_limit_delay)
            
            result = response.json()
            return result.get("recommendedPapers", [])
            
        except requests.exceptions.RequestException as e:
            print(f"Error getting recommendations: {e}")
            return [] 