"""
Elasticsearch document definition for Post.
Does NOT store trending_score in ES.
"""

from elasticsearch_dsl import Document, Text, Keyword, Date, Integer


class PostDocument(Document):
    content = Text(analyzer="standard")
    category_slug = Keyword()
    author_username = Keyword()
    author_id = Integer()
    created_at = Date()

    class Index:
        name = "posts"
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }

    @classmethod
    def from_post(cls, post):
        """Create a PostDocument from a Post model instance."""
        doc = cls(
            meta={"id": post.pk},
            content=post.content,
            category_slug=post.category.slug,
            author_username=post.author.username,
            author_id=post.author_id,
            created_at=post.created_at,
        )
        return doc

