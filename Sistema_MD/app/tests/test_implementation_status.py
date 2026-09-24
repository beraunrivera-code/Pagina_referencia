from unittest import TestCase
from conversion.implementation_status import implementation_report


class ImplementationStatusTests(TestCase):
    def test_all_62_requirements_stay_visible_and_partial_is_not_complete(self):
        result=implementation_report()
        self.assertEqual(result['total'],62)
        self.assertEqual(len({row['id'] for row in result['tasks']}),62)
        self.assertFalse(result['complete'])
        for key in ('F3.02','F4.03','F5.08','F7.06'):
            row=next(row for row in result['tasks'] if row['id']==key)
            self.assertNotEqual(row['state'],'aceptada')
