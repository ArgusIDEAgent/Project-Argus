"""Independent Phase 6 normalization and rank-fusion contracts."""
import unittest

from knowledge_base.retrieval import fuse_rankings, plan_query


class PlanningTests(unittest.TestCase):
    def test_supported_intents_preserve_case_sensitive_identifiers(self):
        cases = [
            (' "Who calls `validateToken`?" ','callers','validateToken',''),
            ('What does login call?','callees','login',''),
            ('What breaks if I change validateToken?','impact','validateToken',''),
            ('What happens if I modify src/auth.ts?','dependents','src/auth.ts','src/auth.ts'),
            ('imports of src/login.ts','imports','src/login.ts','src/login.ts'),
            ('What does src/login.ts import?','imports','src/login.ts','src/login.ts'),
            ('Which files depend on src/auth.ts?','dependents','src/auth.ts','src/auth.ts'),
            ('Where is validateToken defined?','definition','validateToken',''),
            ('Explain function validateToken','explain','validateToken',''),
            ('Explain README.md','explain','README.md','README.md'),
            ('Where are resource permissions checked?','explore','',''),
        ]
        for query,intent,reference,file in cases:
            with self.subTest(query=query):
                plan=plan_query(query)
                self.assertEqual((plan.intent,plan.reference,plan.file),(intent,reference,file))
        self.assertEqual(plan_query('Where is authentication checked?').keywords,'authentication')

    def test_empty_queries_and_question_punctuation_are_safe(self):
        for query in ('','   ','""','` `','x'*2001):
            with self.assertRaises(ValueError): plan_query(query)
        self.assertEqual(plan_query('  `validateToken`  ').reference,'validateToken')
        self.assertEqual(plan_query('what does login do?').intent,'explore')
        self.assertEqual(plan_query('a'*64).reference,'a'*64)
        self.assertEqual(plan_query('解释 `验证`').reference,'验证')


class FusionTests(unittest.TestCase):
    def item(self,name,score=0):
        return dict(entityId=name,path=name+'.ts',startLine=1,endLine=2,sourceHash='hash',confidence=score)

    def test_raw_score_scales_never_affect_fusion(self):
        a,b,c=self.item('a',1e30),self.item('b',-999),self.item('c',.9)
        results=fuse_rankings({'keyword':[a,b],'semantic':[b,c]})
        self.assertEqual([r['entityId'] for r in results],['b','a','c'])
        self.assertAlmostEqual(results[0]['fusionScore'],1/62+1/61)
        self.assertEqual(results[0]['retrievalMethods'],['keyword','semantic'])

    def test_duplicate_channel_hits_vote_once_and_ties_are_stable(self):
        a,b=self.item('a'),self.item('b')
        result=fuse_rankings({'keyword':[a,a,b]})
        self.assertAlmostEqual(result[0]['fusionScore'],1/61)
        self.assertEqual(len(result),2)
        self.assertEqual([r['entityId'] for r in fuse_rankings({'keyword':[b],'semantic':[a]})],['a','b'])


if __name__=='__main__': unittest.main()
